"""Optional PPO challenger checks; core installations skip the RL tests."""

import numpy as np
import pytest

torch = pytest.importorskip("torch")
gym = pytest.importorskip("gymnasium")
sb3 = pytest.importorskip("stable_baselines3")

from research_bot.ensemble_agent import LSTMFeatureExtractor, create_agent


@pytest.fixture(autouse=True)
def _single_torch_thread():
    previous = torch.get_num_threads()
    torch.set_num_threads(1)
    yield
    torch.set_num_threads(previous)


class _TinySequenceEnv(gym.Env):
    """Bounded local sequence environment for the real PPO serialization path."""

    observation_space = gym.spaces.Box(-1.0, 1.0, (5, 3), dtype=np.float32)
    action_space = gym.spaces.Discrete(3)

    def _observation(self):
        return np.full((5, 3), self.steps / 32.0, dtype=np.float32)

    def reset(self, *, seed=None, options=None):
        super().reset(seed=seed)
        self.steps = 0
        return self._observation(), {}

    def step(self, action):
        self.steps += 1
        reward = 0.1 if int(action) == 1 else -0.1
        return self._observation(), reward, self.steps == 16, False, {}


def test_lstm_receives_the_full_time_axis():
    space = gym.spaces.Box(-np.inf, np.inf, (7, 5), dtype=np.float32)
    extractor = LSTMFeatureExtractor(space, features_dim=16)
    observations = torch.randn(2, 7, 5)
    received = []
    hook = extractor.lstm.register_forward_pre_hook(
        lambda _module, inputs: received.append(tuple(inputs[0].shape))
    )
    try:
        output = extractor(observations)
    finally:
        hook.remove()

    assert received == [(2, 7, 5)]
    assert output.shape == (2, 16)
    assert torch.isfinite(output).all()
    assert extractor.lstm.input_size == 5
    assert not extractor.lstm.bidirectional


def test_lstm_prefix_outputs_do_not_depend_on_later_observations():
    space = gym.spaces.Box(-np.inf, np.inf, (7, 3), dtype=np.float32)
    extractor = LSTMFeatureExtractor(space)
    observations = torch.randn(2, 7, 3)
    changed = observations.clone()
    changed[:, 4:] += 100.0
    with torch.no_grad():
        original_outputs, _ = extractor.lstm(observations)
        changed_outputs, _ = extractor.lstm(changed)
    torch.testing.assert_close(original_outputs[:, :4], changed_outputs[:, :4])


@pytest.mark.parametrize("shape", [(5,), (5, 3, 2), (0, 3)])
def test_extractor_rejects_non_sequence_observation_spaces(shape):
    space = gym.spaces.Box(-np.inf, np.inf, shape, dtype=np.float32)
    with pytest.raises(ValueError, match="observation"):
        LSTMFeatureExtractor(space)


@pytest.mark.parametrize(
    ("n_steps", "batch_size"),
    [(0, 64), (1, 64), (128, 1), (8, 16), (7, 2), (8.5, 2), (8, True)],
)
def test_rollout_configuration_is_validated(n_steps, batch_size):
    with pytest.raises(ValueError, match="n_steps|batch_size"):
        create_agent(_TinySequenceEnv(), n_steps=n_steps, batch_size=batch_size)


def test_real_ppo_training_prediction_and_model_roundtrip(tmp_path):
    env = _TinySequenceEnv()
    model = create_agent(env)
    same_seed_model = create_agent(_TinySequenceEnv())
    for actual, expected in zip(
        model.policy.parameters(), same_seed_model.policy.parameters(), strict=True
    ):
        torch.testing.assert_close(actual, expected)
    same_seed_model.get_env().close()

    assert model.device.type == "cpu"
    assert model.n_steps == 128
    assert model.batch_size == 64
    model.n_epochs = 1  # One actual rollout/update exercises the integration seam.
    model.learn(total_timesteps=128)
    assert model.num_timesteps == 128
    observation, _ = env.reset(seed=42)
    action, _ = model.predict(observation, deterministic=True)
    assert env.action_space.contains(int(action))

    destination = tmp_path / "ensemble_agent"
    model.save(destination)
    loaded = sb3.PPO.load(destination, device="cpu")
    loaded_action, _ = loaded.predict(observation, deterministic=True)
    np.testing.assert_array_equal(action, loaded_action)
    for actual, expected in zip(
        loaded.policy.parameters(), model.policy.parameters(), strict=True
    ):
        torch.testing.assert_close(actual, expected)
    model.get_env().close()
