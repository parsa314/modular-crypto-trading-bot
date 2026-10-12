"""Run all ten frozen rules, canonical hypothesis journal and net return matrix."""
from __future__ import annotations
import argparse,json
from contextlib import closing
from hashlib import sha256
from pathlib import Path
import pandas as pd
from research_bot.hypothesis import canonical_json
from research_bot.completion.journal import TradeJournal
from research_bot.ten_strategies.protocol import TenConfig,IDS
from research_bot.ten_strategies.replay import build_matrix
from research_bot.ten_strategies.statistics import diagnostics,rejection_gates
from research_bot.four_systems.binance_history import read_research_csv
from scripts.run_four_systems_research import synthetic_bars

def run(csv: Path | None,output: Path,journal_path: Path,synthetic: bool=False) -> dict:
    if (csv is None)==(not synthetic):raise ValueError("choose explicit synthetic or an existing CSV")
    if not synthetic and (csv is None or not csv.is_file()):
        raise ValueError(f"HISTORICAL_INPUT_NOT_FOUND: {csv}")
    config=TenConfig();frame=synthetic_bars() if synthetic else read_research_csv(csv)
    source=Path(__file__).resolve().parents[1]/"research_bot"/"ten_strategies"
    identities={p.name:sha256(p.read_bytes()).hexdigest() for p in sorted(source.glob("*.py"))}
    identities["runner"]=sha256(Path(__file__).read_bytes()).hexdigest()
    implementation=sha256(canonical_json(identities).encode()).hexdigest()
    dataset=sha256(canonical_json(frame.astype(str).to_dict("records")).encode()).hexdigest()
    lock={"protocol":config.document(),"input_sha256":dataset,"implementation_sha256":implementation,
        "data_class":"SYNTHETIC" if synthetic else "HISTORICAL_BACKFILL","preregistered":False}
    output.parent.mkdir(parents=True,exist_ok=True);journal_path.parent.mkdir(parents=True,exist_ok=True)
    locked=output.with_suffix(".protocol.json");encoded=canonical_json(lock)+"\n"
    if locked.exists():
        if locked.read_text()!=encoded:raise ValueError("locked protocol/data/code changed; choose a new output namespace")
    else:
        with locked.open("x") as stream:stream.write(encoded)
    experiment="TEN_RULES_1:"+sha256(encoded.encode()).hexdigest()
    with closing(TradeJournal(journal_path,experiment)) as journal:
        with journal.atomic():matrix,report=build_matrix(frame,config,journal,implementation)
        stats=diagnostics(matrix,config);report.update({"statistics":stats,"gates":rejection_gates(report,stats,config,synthetic),
            "experiment_id":experiment,"evidence_state":"SYNTHETIC" if synthetic else "HISTORICAL_DIAGNOSTIC",
            "dsr_status":stats["dsr_status"],"pbo_status":stats["pbo_status"],"journal_events":len(journal.events())+(0 if journal.events("TEN_RESEARCH_REPORT") else 1),
            "locked_protocol_file":str(locked),"risk_limits_increased":False,"all_10_tested":True})
        journal.append("report","TEN_RESEARCH_REPORT",frame.available_at.iloc[-1].to_pydatetime(),report)
    matrix.to_csv(output.with_suffix(".net_returns.csv"),float_format="%.17g")
    report["matrix_sha256"]=sha256(output.with_suffix(".net_returns.csv").read_bytes()).hexdigest()
    output.write_text(json.dumps(report,indent=2,allow_nan=False)+"\n")
    lines=["# Ten-rule research replay", "", "Evidence: "+report["evidence_state"],"",
        "Declared costs; no alpha/PAPER/LIVE claim. Official DSR: "+report["dsr_status"]+".","",
        "|Strategy|Closed trades|Net account return|MaxDD|Gate|","|---|---:|---:|---:|---|"]
    for s in IDS:
        r=report["results"][s];lines.append("|"+s+"|"+str(r["closed_trades"])+"|"+r["net_total_return"]+"|"+r["max_drawdown"]+"|"+report["gates"][s]["status"]+"|")
    output.with_suffix(".md").write_text("\n".join(lines)+"\n")
    return report

def main() -> int:
    p=argparse.ArgumentParser(description=__doc__);g=p.add_mutually_exclusive_group(required=True)
    g.add_argument("--synthetic",action="store_true");g.add_argument("--csv",type=Path)
    p.add_argument("--output",type=Path,required=True);p.add_argument("--journal",type=Path,required=True)
    a=p.parse_args();print(json.dumps({"event":"TEN_RESEARCH_START","RESEARCH_ONLY":True,"PAPER_EXECUTION":False,"LIVE_EXECUTION":False}),flush=True)
    try:
        r=run(a.csv,a.output,a.journal,a.synthetic)
        summary={k:r[k] for k in ("evidence_state","rows","columns","orders_placed","dsr_status","pbo_status","matrix_sha256","journal_events")}
        summary["results"]={s:{k:r["results"][s][k] for k in ("closed_trades","hypotheses","net_total_return","max_drawdown")} for s in IDS}
        summary["gates"]=r["gates"];print(json.dumps(summary,allow_nan=False),flush=True)
    except Exception as e:
        a.output.parent.mkdir(parents=True,exist_ok=True)
        r={"status":"FAILED_DATA_OR_PROTOCOL","reason":str(e),"synthetic_fallback":False,"execution_authorized":False,"dsr_status":"NOT_RUN","pbo_status":"NOT_RUN"}
        # Never overwrite a successful prior output on a failed/reconfigured replay.
        failure=a.output.with_suffix(".failed.json");failure.write_text(json.dumps(r,indent=2)+"\n")
        print(json.dumps(r),flush=True);return 1
    return 0

if __name__=="__main__":raise SystemExit(main())
