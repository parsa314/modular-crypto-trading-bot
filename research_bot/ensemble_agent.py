"""Optional, CPU-only PPO challenger with a causal sequence feature encoder.

The LSTM reads a complete past observation window on each call. It is not a
recurrent PPO policy and does not carry hidden state between policy decisions.
Importing this module does not require the optional RL dependencies; constructing
an agent or encoder does. Nothing here connects to an exchange or enables orders.
"""

from __future__ import annotations

from numbers import Integral

_RL_IMPORT_ERROR = None
try:
    import torch
    from torch import nn
    from stable_baselines3 import PPO
    from stable_baselines3.common.torch_layers import BaseFeaturesExtractor
except ImportError as exc:  # pragma: no cover - depends on the installed extras
    _RL_IMPORT_ERROR = exc
    torch = None
    nn = None
    PPO = None
    BaseFeaturesExtractor = object


def _require_rl() -> None:
    if _RL_IMPORT_ERROR is not None:
        raise RuntimeError(
            "The ensemble PPO challenger requires PyTorch, Gymnasium and "
            "Stable-Baselines3; install the project's 'rl' extra."
        ) from _RL_IMPORT_ERROR


def _integer_at_least(value, name: str, minimum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, Integral) or value < minimum:
        raise ValueError(f"{name} must be an integer >= {minimum}")
    return int(value)


class LSTMFeatureExtractor(BaseFeaturesExtractor):
    """Encode (batch, lookback, features) without collapsing the time axis."""

    def __init__(self, observation_space, features_dim: int = 64):
        _require_rl()
        shape = observation_space.shape
        if shape is None or len(shape) != 2 or any(size <= 0 for size in shape):
            raise ValueError("observation space must have shape (lookback, features)")
        features_dim = _integer_at_least(features_dim, "features_dim", 1)
        super().__init__(observation_space, features_dim)
        self.lstm = nn.LSTM(
            input_size=shape[-1],
            hidden_size=64,
            batch_first=True,
            bidirectional=False,
        )
        self.fc = nn.Sequential(nn.Linear(64, features_dim), nn.ReLU())

    def forward(self, observations):
        if (
            observations.ndim != 3
            or observations.shape[1] == 0
            or observations.shape[-1] != self.lstm.input_size
        ):
            raise ValueError("observations must have shape (batch, lookback, features)")
        sequence, _ = self.lstm(observations)
        return self.fc(sequence[:, -1, :])


def create_agent(env, *, seed: int = 42, n_steps: int = 128, batch_size: int = 64):
    """Build a seeded PPO agent for an offline environment on the CPU.

    ``batch_size`` must divide ``n_steps * num_envs`` so every minibatch contains
    the requested number of samples. Both rollout and minibatch counts must be
    at least two for PPO advantage normalization.
    """
    seed = _integer_at_least(seed, "seed", 0)
    n_steps = _integer_at_least(n_steps, "n_steps", 2)
    batch_size = _integer_at_least(batch_size, "batch_size", 2)
    num_envs = _integer_at_least(getattr(env, "num_envs", 1), "num_envs", 1)
    rollout_size = n_steps * num_envs
    if batch_size > rollout_size or rollout_size % batch_size:
        raise ValueError("batch_size must divide and not exceed n_steps * num_envs")
    _require_rl()
    return PPO(
        "MlpPolicy",
        env,
        seed=seed,
        device="cpu",
        verbose=0,
        learning_rate=3e-4,
        n_steps=n_steps,
        batch_size=batch_size,
        n_epochs=10,
        gamma=0.99,
        gae_lambda=0.95,
        clip_range=0.2,
        policy_kwargs={
            "features_extractor_class": LSTMFeatureExtractor,
            "features_extractor_kwargs": {"features_dim": 64},
            "net_arch": {"pi": [64, 32], "vf": [64, 32]},
        },
    )
