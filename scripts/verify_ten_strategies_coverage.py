"""Verify ten-strategy behavior and executable-statement coverage without exclusions."""
from __future__ import annotations
import argparse,json,sys,trace,unittest
from pathlib import Path

def main() -> int:
    p=argparse.ArgumentParser();p.add_argument("--output",type=Path,required=True);p.add_argument("--threshold",type=float,default=80)
    a=p.parse_args();root=Path(__file__).resolve().parents[1];sys.path.insert(0,str(root))
    tracer=trace.Trace(count=True,trace=False,ignoredirs=[sys.base_prefix])
    def check() -> unittest.TestResult:
        return unittest.TextTestRunner(verbosity=1).run(unittest.defaultTestLoader.discover(str(root/"tests"),pattern="test_ten_strategies*.py"))
    result=tracer.runfunc(check);hits={};names={}
    for (filename,line),count in tracer.results().counts.items():
        if filename not in names:names[filename]=str(Path(filename).resolve())
        if count:hits.setdefault(names[filename],set()).add(line)
    modules={}
    for file in sorted((root/"research_bot"/"ten_strategies").glob("*.py")):
        if file.name=="__init__.py":continue
        executable=set(trace._find_executable_linenos(str(file)));covered=executable&hits.get(str(file.resolve()),set())
        modules[str(file.relative_to(root))]={"coverage_percent":round(100*len(covered)/len(executable),2),
            "covered":len(covered),"statements":len(executable),"missing":sorted(executable-covered)}
    report={"tests":result.testsRun,"success":result.wasSuccessful(),"modules":modules,"method":"stdlib executable statement coverage"}
    a.output.parent.mkdir(parents=True,exist_ok=True);a.output.write_text(json.dumps(report,indent=2)+"\n")
    print(json.dumps(report));return 0 if result.wasSuccessful() and all(m["coverage_percent"]>a.threshold for m in modules.values()) else 1

if __name__=="__main__":raise SystemExit(main())
