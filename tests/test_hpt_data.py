from dataclasses import replace
import unittest
import numpy as np
from research_bot.hpt_data import make_hpt_windows,purged_hpt_walk_forward


class HPTDataTests(unittest.TestCase):
    def setUp(self): self.prices=100*np.exp(np.arange(120)*.003+np.sin(np.arange(120))*.01)

    def test_future_target_is_not_in_features_and_multibar_return_is_complete(self):
        windows=make_hpt_windows(self.prices,sequence_length=4,horizon=3)
        t=int(windows.decision_indices[0]); end=int(windows.label_end_indices[0])
        self.assertEqual((t,end),(4,7))
        self.assertAlmostEqual(float(windows.targets[0,0]),np.log(self.prices[7]/self.prices[4]),places=7)
        changed=self.prices.copy(); changed[5:]*=1.3
        future=make_hpt_windows(changed,sequence_length=4,horizon=3)
        np.testing.assert_array_equal(windows.features[0],future.features[0])
        self.assertNotEqual(windows.targets[0,0],future.targets[0,0])
        self.assertEqual(windows.targets.shape[1],1)
        with self.assertRaises(ValueError): windows.features[0,0,0]=1

    def test_expanding_folds_purge_label_endpoints_and_do_not_overlap_tests(self):
        windows=make_hpt_windows(self.prices,sequence_length=8,horizon=4)
        folds=purged_hpt_walk_forward(windows,20,10,10)
        for fold in folds:
            self.assertLess(windows.label_end_indices[fold.train].max(),windows.decision_indices[fold.validation].min())
            self.assertLess(windows.label_end_indices[fold.validation].max(),windows.decision_indices[fold.test].min())
        self.assertLess(folds[0].test.max(),folds[1].test.min())
        self.assertGreater(len(folds[1].train),len(folds[0].train))

    def test_invalid_values_and_insufficient_support_are_rejected(self):
        for values in ([100,0,100],[100,float('nan'),100],[[100,101]],[]):
            with self.assertRaises(ValueError): make_hpt_windows(values,sequence_length=1)
        for kwargs in ({'sequence_length':True},{'horizon':0},{'z_window':-1},{'rsi_period':0}):
            with self.assertRaises(ValueError): make_hpt_windows(self.prices,**kwargs)
        windows=make_hpt_windows(self.prices)
        with self.assertRaises(ValueError): purged_hpt_walk_forward(windows,1000,10,10)
        with self.assertRaises(ValueError): purged_hpt_walk_forward(windows,10,10,10,step=9)
        with self.assertRaises(ValueError): purged_hpt_walk_forward(replace(windows,targets=windows.targets[:,0]),10,10,10)

    def test_constant_and_monotone_inputs_are_finite(self):
        for prices in ([100]*100,np.arange(1,101),np.arange(101,1,-1)):
            windows=make_hpt_windows(prices)
            self.assertTrue(np.isfinite(windows.features).all())
            self.assertTrue(np.isfinite(windows.targets).all())


if __name__ == '__main__': unittest.main()
