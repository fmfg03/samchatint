import json,sys,xml.etree.ElementTree as ET
from pathlib import Path
r=Path(sys.argv[1]); cases=ET.parse(r/'results.xml').findall('.//testcase')
assert cases and not any(c.find(x) is not None for c in cases for x in ('failure','error','skipped'))
assert sum('test_identity_binding_composition' in c.get('classname','') for c in cases)==3
cov=json.loads((r/'coverage.json').read_text())['files']
required=['batisenal_case_bridge.py','batisenal_pilot.py','batisenal_case_service.py','batisenal_case_authority.py','batisenal_pilot_routes.py']
summary={}
for name in required:
 matches=[v['summary'] for k,v in cov.items() if k.endswith('/'+name)]
 assert len(matches)==1,name
 s=matches[0];assert s['covered_lines']*100>=s['num_statements']*85,name
 summary[name]=s
print(json.dumps({'tests':len(cases),'skipped':0,'pilot_coverage':summary}))
