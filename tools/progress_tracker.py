#!/usr/bin/env python3
"""Validate and summarize hierarchical progress plans using only the standard library."""
import argparse, json, sys
from datetime import datetime, timezone
from pathlib import Path

VALID={"PLANNED","IMPLEMENTED","TESTED","VERIFIED","BLOCKED"}
FACTORS={"PLANNED":0.0,"IMPLEMENTED":0.4,"TESTED":0.7,"VERIFIED":1.0,"BLOCKED":0.0}

class PlanError(ValueError): pass

def validate(p):
    if p.get("schema_version") != 1 or not isinstance(p.get("nodes"),list): raise PlanError("invalid schema")
    nodes=p["nodes"]; by={}
    for n in nodes:
        i=n.get("id")
        if not i or i in by: raise PlanError("missing or duplicate node id: "+str(i))
        by[i]=n
        if n.get("status") not in VALID: raise PlanError(f"{i}: invalid status")
        if not isinstance(n.get("weight"),(int,float)) or isinstance(n.get("weight"),bool) or n["weight"]<=0: raise PlanError(f"{i}: invalid weight")
        if not n.get("acceptance_criteria"): raise PlanError(f"{i}: acceptance criteria required")
        for e in n.get("evidence",[]):
            if not all(e.get(k) for k in ("type","status","ref")): raise PlanError(f"{i}: incomplete evidence")
        if n["status"]=="VERIFIED" and not any(e.get("status")=="PASS" and e.get("ref") for e in n.get("evidence",[])): raise PlanError(f"{i}: VERIFIED requires PASS evidence")
        if n["status"]=="BLOCKED" and not all(n.get("blocker",{}).get(k) for k in ("reason","resolution")): raise PlanError(f"{i}: BLOCKED requires reason and resolution")
    roots=[n for n in nodes if n.get("parent_id") is None]
    if len(roots)!=1: raise PlanError("exactly one root required")
    for i,n in by.items():
        if n.get("parent_id") is not None and n["parent_id"] not in by: raise PlanError(f"{i}: unknown parent")
        for k in ("children","dependencies"):
            for x in n.get(k,[]):
                if x not in by: raise PlanError(f"{i}: unknown {k} reference {x}")
        for c in n.get("children",[]):
            if by[c].get("parent_id")!=i: raise PlanError(f"{i}: child parent mismatch")
        if n.get("children") and abs(sum(by[c]["weight"] for c in n["children"])-100)>1e-6: raise PlanError(f"{i}: child weights must sum to 100")
    def graph(i,edge,active,done):
        if i in active: raise PlanError("cycle detected in "+edge)
        if i in done: return
        active.add(i)
        for x in by[i].get(edge,[]): graph(x,edge,active,done)
        active.remove(i); done.add(i)
    for edge in ("children","dependencies"):
        done=set()
        for i in by: graph(i,edge,set(),done)
    def state(i):
        n=by[i]
        if not n.get("children"): return n["status"]
        ss=[state(c) for c in n["children"]]
        if all(x=="VERIFIED" for x in ss): return "VERIFIED"
        if "BLOCKED" in ss: return "BLOCKED"
        if any(x in {"TESTED","VERIFIED"} for x in ss): return "TESTED"
        if "IMPLEMENTED" in ss: return "IMPLEMENTED"
        return "PLANNED"
    for i,n in by.items():
        if n["status"]=="VERIFIED" and any(state(d)!="VERIFIED" for d in n.get("dependencies",[])): raise PlanError(f"{i}: dependency is not VERIFIED")
    return by,roots[0]["id"],state

def summarize(p):
    by,root,state=validate(p)
    def readiness(n):
        ds=n.get("dependencies",[])
        if not ds:return "NONE"
        ss=[state(d) for d in ds]
        if all(x=="VERIFIED" for x in ss):return "READY"
        if "BLOCKED" in ss:return "BLOCKED_BY_DEPENDENCY"
        return "WAITING_FOR_DEPENDENCY"
    def calc(i):
        n=by[i]
        if n.get("children"):
            xs=[(by[c]["weight"]/100,calc(c)) for c in n["children"]]
            return {k:sum(w*v[k] for w,v in xs) for k in ("work","accepted","evidence")}
        return {"work":FACTORS[n["status"]],"accepted":float(n["status"]=="VERIFIED"),"evidence":float(any(e.get("status") in {"PRESENT","PASS"} and e.get("ref") for e in n.get("evidence",[])))}
    m=calc(root); leaves=[n for n in p["nodes"] if not n.get("children")]; blocked=[n for n in leaves if n["status"]=="BLOCKED"]
    return {"project_id":p.get("project_id"),"title":p.get("title"),"baseline":p.get("baseline",{}),
      "work_progress_percent":round(m["work"]*100,2),"acceptance_progress_percent":round(m["accepted"]*100,2),"evidence_coverage_percent":round(m["evidence"]*100,2),
      "blocked_count":len(blocked),"blocked":[{"id":n["id"],"title":n["title"],**n["blocker"]} for n in blocked],"unverified_leaf_count":sum(n["status"]!="VERIFIED" for n in leaves),
      "nodes":[{"id":n["id"],"parent_id":n.get("parent_id"),"title":n["title"],"status":n["status"],"effective_status":state(n["id"]),"dependency_readiness":readiness(n),"weight":n["weight"],"dependencies":n.get("dependencies",[]),"metrics":{k:round(v*100,2) for k,v in calc(n["id"]).items()},"evidence_count":len(n.get("evidence",[]))} for n in p["nodes"]]}

def render(s):
    b=s["baseline"]; lines=["# Progress Snapshot — "+s["title"],"",f"- Project: {s['project_id']}",f"- Repository: {b.get('repository','unknown')}",f"- Baseline branch: {b.get('branch','unknown')}",f"- Baseline commit: {b.get('commit','unknown')}",f"- Generated UTC: {datetime.now(timezone.utc).isoformat(timespec='seconds')}",f"- Work progress: **{s['work_progress_percent']}%**",f"- Acceptance progress: **{s['acceptance_progress_percent']}%**",f"- Evidence coverage: **{s['evidence_coverage_percent']}%**",f"- Blocked tasks: **{s['blocked_count']}**",f"- Unverified leaf tasks: **{s['unverified_leaf_count']}**","",
    "| ID | Parent | Status | Dependency readiness | Weight | Work | Accepted | Evidence | Task |","|---|---|---|---|---:|---:|---:|---:|---|"]
    for n in s["nodes"]:
        vals=[n["id"],n.get("parent_id") or "—",n["effective_status"],n["dependency_readiness"],str(n["weight"]),str(n["metrics"]["work"])+"%",str(n["metrics"]["accepted"])+"%",str(n["evidence_count"]),n["title"].replace("|","\\|")]
        lines.append("| "+" | ".join(vals)+" |")
    if s["blocked"]:
        lines+=["","## Blockers",""]
        for n in s["blocked"]:lines.append("- **"+n["id"]+" — "+n["title"]+"**: "+n["reason"]+" Next: "+n["resolution"])
    lines+=["","Work progress is an estimate. Acceptance counts only VERIFIED leaves with PASS evidence. Old CI results do not verify another commit or real local runtime.",""]
    return chr(10).join(lines)

def main(argv=None):
    ap=argparse.ArgumentParser(description=__doc__); ap.add_argument("command",choices=["validate","report","snapshot"]); ap.add_argument("--plan",type=Path,default=Path("progress/project_plan.json")); ap.add_argument("--format",choices=["text","json","markdown"],default="text"); ap.add_argument("--out",type=Path); a=ap.parse_args(argv)
    try:
        p=json.loads(a.plan.read_text(encoding="utf-8")); validate(p); s=summarize(p)
        if a.command=="validate": print(f"PASS: plan valid; {len(p['nodes'])} nodes")
        elif a.command=="snapshot":
            if not a.out: ap.error("snapshot requires --out")
            a.out.parent.mkdir(parents=True,exist_ok=True); a.out.write_text(render(s),encoding="utf-8"); print("PASS: snapshot written to "+str(a.out))
        elif a.format=="json": print(json.dumps(s,ensure_ascii=False,indent=2))
        elif a.format=="markdown": print(render(s))
        else:
            print(s["title"]); print(f"Work {s['work_progress_percent']}% | Acceptance {s['acceptance_progress_percent']}% | Evidence {s['evidence_coverage_percent']}%"); print(f"Blocked {s['blocked_count']} | Unverified leaves {s['unverified_leaf_count']}")
            for n in s["blocked"]: print("BLOCKED "+n["id"]+": "+n["reason"]+" Next: "+n["resolution"])
        return 0
    except (OSError,json.JSONDecodeError,PlanError) as e: print("ERROR: "+str(e),file=sys.stderr); return 2

if __name__=="__main__": raise SystemExit(main())
