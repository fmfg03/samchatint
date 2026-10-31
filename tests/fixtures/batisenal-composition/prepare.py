"""Unpack verified historical fixture into a new directory, never runtime paths."""
import hashlib,json,pathlib,shutil,stat,subprocess,sys,zipfile
root=pathlib.Path(__file__).resolve().parent
archive=root/'pilot-source.zip'
assert hashlib.sha256(archive.read_bytes()).hexdigest()=='b8d89c6d0b1c7f04b48cce0de6e54827740ac1e70c53efa657ed094a21f46b63'
dest=pathlib.Path(sys.argv[1]);dest.mkdir(mode=0o700,exist_ok=False)
with zipfile.ZipFile(archive) as z:
 for i in z.infolist():
  p=pathlib.PurePosixPath(i.filename)
  assert not p.is_absolute() and '..' not in p.parts and not stat.S_ISLNK(i.external_attr>>16)
 z.extractall(dest)
pkg=dest/'samchat-pilot-compatible-source-20261026'
subprocess.run([sys.executable,str(pkg/'verify_source.py')],check=True)
checkout=pkg/'checkout'
for name,expected in json.loads((root/'overlay-hashes.json').read_text()).items():
 source=root/name
 assert hashlib.sha256(source.read_bytes()).hexdigest()==expected
 if name.startswith('adapter_overlay/'):
  target=checkout/'src/samchat/readonly_gateway'/source.name
  assert not target.exists()
 else:target=checkout/'tests/batisenal_pilot'/source.name
 shutil.copyfile(source,target)
shutil.move(str(checkout), str(dest/'c'))
# Keep the historical archive and identity overlay unchanged. Only this fresh
# fixture checkout receives the separately reviewed modern transport delta.
manifest=json.loads((root/'modern-hashes.json').read_text())
contract=dest/'modern-tests'
contract.mkdir()
ui=pathlib.Path('src/devnous/gastos/routes/batisenal_pilot_routes.py')
baseline=contract/'baseline'
(baseline/ui).parent.mkdir(parents=True)
shutil.copyfile(dest/'c'/ui,baseline/ui)
def apply_patch(path,target,expected):
 assert hashlib.sha256(path.read_bytes()).hexdigest()==expected
 for mode in (['--check'],[]):
  subprocess.run(['git','-c','core.autocrlf=false','apply','--unidiff-zero',*mode,str(path.resolve())],
                 cwd=target,check=True)
apply_patch(root/'presentation-baseline.patch',baseline,manifest['presentation_baseline_patch_sha256'])
assert hashlib.sha256((baseline/ui).read_bytes()).hexdigest()==manifest['baseline_route_sha256']
for row in manifest['changes']:
 target=dest/'c'/row['path']
 if row['before_sha256'] is None: assert not target.exists(),row['path']
 else: assert hashlib.sha256(target.read_bytes()).hexdigest()==row['before_sha256'],row['path']
apply_patch(root/'modern-integration.patch',dest/'c',manifest['integration_patch_sha256'])
for row in manifest['changes']:
 assert hashlib.sha256((dest/'c'/row['path']).read_bytes()).hexdigest()==row['after_sha256'],row['path']
for name,expected in manifest['contract_files'].items():
 assert pathlib.PurePosixPath(name).name==name
 source=root/'modern_contract'/name
 assert hashlib.sha256(source.read_bytes()).hexdigest()==expected
 shutil.copyfile(source,contract/name)
print(dest/'c')
