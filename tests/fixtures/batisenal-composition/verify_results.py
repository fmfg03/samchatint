import json,sys,xml.etree.ElementTree as ET
from pathlib import Path
r=Path(sys.argv[1]); cases=ET.parse(r/'results.xml').findall('.//testcase')
assert len(cases)==55 and not any(c.find(x) is not None for c in cases for x in ('failure','error','skipped'))
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

modern=json.loads((r/'modern/test-results.json').read_text())
assert modern['run']==33 and modern['passed']==33
assert modern['failures']==0 and modern['errors']==0 and modern['skipped']==0
modern_cov=json.loads((r/'modern/coverage-modern.json').read_text())['files']
modern_summary={}
for suffix in ('/batisenal_transport/framing.py','/batisenal_transport/http.py'):
 matches=[v['summary'] for k,v in modern_cov.items() if k.replace(chr(92),'/').endswith(suffix)]
 assert len(matches)==1,suffix
 s=matches[0]
 assert s['covered_lines']*100>=s['num_statements']*85,suffix
 modern_summary[suffix]=s
print(json.dumps({'modern_tests':modern,'modern_coverage':modern_summary}))
