"""Frozen chronological ablations; fit/scaler/PCA never observe test outcomes."""
from __future__ import annotations

from dataclasses import dataclass, asdict
from hashlib import sha256
from pathlib import Path
from typing import Sequence
import numpy as np
import pandas as pd
from sklearn.decomposition import PCA
from sklearn.isotonic import IsotonicRegression
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler
from sklearn.metrics import brier_score_loss, log_loss

from ..hypothesis import canonical_json
from .features import FeatureConfig, build_features, feature_families


@dataclass(frozen=True)
class ExperimentConfig:
    purpose: str = 'HISTORICAL_RESEARCH'
    horizon: int = 4
    embargo: int = 1
    min_train: int = 300
    min_calibration: int = 1000
    test_size: int = 200
    min_class: int = 30
    cost_fraction: float = .0024
    seed: int = 2026

    def __post_init__(self) -> None:
        if self.purpose not in {'HISTORICAL_RESEARCH','ENGINEERING_SYNTHETIC'}:
            raise ValueError('unsupported experiment purpose')
        for key in ('horizon','embargo','min_train','min_calibration','test_size','min_class','seed'):
            if type(getattr(self,key)) is not int or getattr(self,key)<1:raise ValueError('positive integer protocol fields required')
        if not np.isfinite(self.cost_fraction) or not 0<self.cost_fraction<=.01:
            raise ValueError('explicit positive cost hurdle <=1% required')
        if self.purpose=='HISTORICAL_RESEARCH' and self.min_calibration<1000:
            raise ValueError('isotonic research calibration needs >=1000 events; small fixtures are engineering only')


@dataclass(frozen=True)
class Fold:
    train: tuple[int,...]
    calibration: tuple[int,...]
    test: tuple[int,...]


def prepare_dataset(frame: pd.DataFrame, config: ExperimentConfig, features: FeatureConfig = FeatureConfig()) -> pd.DataFrame:
    x=build_features(frame,features)
    x['gross_forward_return']=x.close.shift(-config.horizon)/x.close-1
    x['label_end_available_at']=x.available_at.shift(-config.horizon)
    x['favorable']=(x.gross_forward_return>config.cost_fraction).astype(int)
    # All variants share exactly the same warmup and primary setup cohort.
    columns=sum(feature_families().values(),())
    x['eligible']=x.setup&x[list(columns)].notna().all(axis=1)&x.gross_forward_return.notna()
    return x


def chronological_folds(dataset: pd.DataFrame, config: ExperimentConfig,
                        features: FeatureConfig = FeatureConfig()) -> tuple[Fold,...]:
    eligible=np.flatnonzero(dataset.eligible.to_numpy())
    folds=[];gap=features.interval*config.embargo
    for start in range(config.min_train+config.min_calibration,len(eligible),config.test_size):
        test=eligible[start:start+config.test_size]
        if len(test)!=config.test_size:break
        before=eligible[:start]
        # Purge by actual label availability, not merely sample-row distances.
        before=before[(dataset.label_end_available_at.iloc[before]<dataset.available_at.iloc[test[0]]-gap).to_numpy()]
        if len(before)<config.min_calibration+config.min_train:continue
        calibration=before[-config.min_calibration:]
        train=before[:-config.min_calibration]
        train=train[(dataset.label_end_available_at.iloc[train]<dataset.available_at.iloc[calibration[0]]-gap).to_numpy()]
        if len(train)<config.min_train:continue
        folds.append(Fold(tuple(map(int,train)),tuple(map(int,calibration)),tuple(map(int,test))))
    if not folds:raise ValueError('insufficient chronological setup support after label purge and embargo')
    return tuple(folds)


def calibration_metrics(labels: Sequence[int], probabilities: Sequence[float]) -> dict:
    y=np.asarray(labels);p=np.asarray(probabilities,dtype=float)
    if y.ndim!=1 or p.shape!=y.shape or not len(y) or not np.isfinite(p).all() or np.any((p<0)|(p>1)) or not np.isin(y,[0,1]).all():
        raise ValueError('aligned binary labels and finite probabilities required')
    ece=0.;reliability=[]
    for i in range(10):
        selected=(p>=i/10)&(p<=(i+1)/10 if i==9 else p<(i+1)/10)
        if selected.any():
            mean=float(p[selected].mean());frequency=float(y[selected].mean());n=int(selected.sum())
            ece+=n/len(y)*abs(mean-frequency)
            reliability.append({'bin':i,'n':n,'probability':mean,'frequency':frequency})
    return {'n':len(y),'brier':float(brier_score_loss(y,p)),
        'log_loss':float(log_loss(y,np.clip(p,1e-12,1-1e-12),labels=[0,1])),
        'ece':ece,'reliability':reliability}


class CalibratedFoldModel:
    """Candidate-outcome probabilities; not an execution/graduation certificate."""
    def __init__(self, columns: tuple[str,...], config: ExperimentConfig, *, pca: bool = False) -> None:
        if not columns or len(set(columns))!=len(columns) or any('forward' in c or c not in sum(feature_families().values(),()) for c in columns):
            raise ValueError('only frozen causal feature columns allowed')
        self.columns,self.config,self.pca=columns,config,pca
        self.fitted=False

    def fit(self, dataset: pd.DataFrame, fold: Fold) -> None:
        if self.fitted:raise ValueError('model fit is immutable; create a new fold model')
        tr,ca,te=(list(v) for v in (fold.train,fold.calibration,fold.test))
        if any(type(i) is not int or not 0<=i<len(dataset) for i in tr+ca+te):
            raise ValueError('fold indices must be valid nonnegative dataset positions')
        if (not tr or not ca or not te or len(set(tr+ca+te))!=len(tr+ca+te)
                or any(v!=sorted(v) for v in (tr,ca,te)) or max(tr)>=min(ca) or max(ca)>=min(te)):
            raise ValueError('disjoint ordered train/calibration/test required')
        if len(tr)<self.config.min_train or len(ca)<self.config.min_calibration:
            raise ValueError('insufficient frozen support')
        # Recheck the availability boundaries even for a caller-created Fold.
        gap=dataset.timestamp.diff().dropna().min()*self.config.embargo
        if (dataset.label_end_available_at.iloc[tr].max()>=dataset.available_at.iloc[ca[0]]-gap
                or dataset.label_end_available_at.iloc[ca].max()>=dataset.available_at.iloc[te[0]]-gap):
            raise ValueError('label leakage across calibration/test boundary')
        for ids in (tr,ca):
            labels=dataset.favorable.iloc[ids].to_numpy()
            if min(int((labels==0).sum()),int((labels==1).sum()))<self.config.min_class:
                raise ValueError('both classes need frozen minimum support')
        xtrain=dataset.iloc[tr][list(self.columns)].to_numpy(float)
        xcal=dataset.iloc[ca][list(self.columns)].to_numpy(float)
        if not np.isfinite(xtrain).all() or not np.isfinite(xcal).all():raise ValueError('finite training/calibration features required')
        steps=[('scale',StandardScaler())]
        if self.pca:steps.append(('reduce',PCA(n_components=.95,svd_solver='full')))
        steps.append(('classifier',LogisticRegression(C=1.,max_iter=500,random_state=self.config.seed)))
        pipeline=Pipeline(steps);pipeline.fit(xtrain,dataset.favorable.iloc[tr])
        raw=pipeline.predict_proba(xcal)[:,1]
        if np.ptp(raw)<=1e-12:raise ValueError('uninformative calibration scores')
        calibrator=IsotonicRegression(out_of_bounds='clip');calibrator.fit(raw,dataset.favorable.iloc[ca])
        identity={'columns':self.columns,'pca':self.pca,'config':asdict(self.config),
            'train':dataset.iloc[tr][list(self.columns)+['favorable','gross_forward_return']].to_dict('split'),
            'calibration':dataset.iloc[ca][list(self.columns)+['favorable']].to_dict('split'),
            'availability':dataset.iloc[tr+ca][['timestamp','available_at','label_end_available_at']].astype(str).to_dict('split')}
        self.model_id=sha256(canonical_json(identity).encode()).hexdigest()
        self.trained_at=dataset.label_end_available_at.iloc[ca].max()
        self.pipeline,self.calibrator=pipeline,calibrator
        self.support=(float(raw.min()),float(raw.max()))
        self.positive_mean=float(dataset.gross_forward_return.iloc[tr].loc[dataset.favorable.iloc[tr].eq(1)].mean())
        self.negative_mean=float(dataset.gross_forward_return.iloc[tr].loc[dataset.favorable.iloc[tr].eq(0)].mean())
        self.training_tail_returns=tuple(float(v) for v in dataset.gross_forward_return.iloc[tr])
        self.calibration_report=calibration_metrics(dataset.favorable.iloc[ca],calibrator.predict(raw))
        self.fitted=True

    def predict(self, rows: pd.DataFrame) -> tuple[np.ndarray,np.ndarray,np.ndarray]:
        if not self.fitted:raise ValueError('untrained model')
        if rows.empty or rows.available_at.min()<=self.trained_at:raise ValueError('prediction must follow all calibration outcomes')
        values=rows[list(self.columns)].to_numpy(float)
        if not np.isfinite(values).all():raise ValueError('non-finite inference feature')
        raw=self.pipeline.predict_proba(values)[:,1]
        support=(raw>=self.support[0])&(raw<=self.support[1])
        return raw,self.calibrator.predict(raw),support


def run_ablation(frame: pd.DataFrame, config: ExperimentConfig = ExperimentConfig(),
                 features: FeatureConfig = FeatureConfig()) -> tuple[dict,pd.DataFrame,list[tuple[str,Fold,CalibratedFoldModel]]]:
    dataset=prepare_dataset(frame,config,features);folds=chronological_folds(dataset,config,features)
    families=feature_families();all_columns=sum(families.values(),())
    variants={**{k:(v,False) for k,v in families.items()},'ALL_FOUR':(all_columns,False),'ALL_FOUR_PCA':(all_columns,True)}
    output=[];models=[]
    for number,fold in enumerate(folds):
        for name,(columns,pca) in variants.items():
            model=CalibratedFoldModel(columns,config,pca=pca)
            try:model.fit(dataset,fold)
            except ValueError as error:
                output.append({'fold':number,'variant':name,'status':'INSUFFICIENT_SUPPORT','reason':str(error)});continue
            rows=dataset.iloc[list(fold.test)];raw,calibrated,support=model.predict(rows)
            labels=rows.favorable.to_numpy()
            baseline=np.full(len(rows),dataset.favorable.iloc[list(fold.train)].mean())
            output.append({'fold':number,'variant':name,'status':'EVALUATED_OOS_ENGINEERING' if config.purpose=='ENGINEERING_SYNTHETIC' else 'EVALUATED_HISTORICAL_CONTEXT',
                'model_id':model.model_id,'train_events':len(fold.train),'calibration_events':len(fold.calibration),
                'test_start':str(rows.available_at.iloc[0]),'test_end':str(rows.available_at.iloc[-1]),
                'raw':calibration_metrics(labels,raw),'calibrated':calibration_metrics(labels,calibrated),
                'base_rate':calibration_metrics(labels,baseline),'out_of_support':int((~support).sum()),
                'pca_components':int(model.pipeline.named_steps['reduce'].n_components_) if pca else None})
            models.append((name,fold,model))
    report={'experiment':'FOUR_SYSTEMS_CAUSAL_CALIBRATION_V1','protocol':asdict(config),
        'implementation_sha256':sha256(canonical_json({p.name:sha256(p.read_bytes()).hexdigest()
            for p in sorted(Path(__file__).parent.glob('*.py'))}).encode()).hexdigest(),
        'feature_protocol':{k:str(v) for k,v in asdict(features).items()},'dataset_sha256':dataset.attrs['dataset_sha256'],
        'protocol_sha256':sha256(canonical_json([asdict(config),{k:str(v) for k,v in asdict(features).items()}]).encode()).hexdigest(),
        'cohort':'same primary setups and label-availability-purged folds across all feature variants',
        'results':output,'feature_families':families,'trials':len(output),'economic_validation':False,
        'dsr_pbo':'NOT_RUN; requires complete costed execution trial matrix, not outcome classification scores',
        'prospective_evidence':False,'paper_graduated':False,'live_execution':False,
        'frozen_v51_changed':False,'best_variant_selected':False}
    return report,dataset,models
