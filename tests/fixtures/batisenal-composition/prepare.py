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
print(dest/'c')
