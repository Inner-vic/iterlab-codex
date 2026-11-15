"""A small startup snapshot: never serve new frontend code from an old host."""
import hashlib
import gzip
import re
from pathlib import Path


class StaticBundle:
    def __init__(self, root):
        root=Path(root).resolve()
        self.files={'/'+p.relative_to(root).as_posix():p.read_bytes()
                    for p in sorted(root.rglob('*')) if p.is_file() and not p.is_symlink()}
        digest=hashlib.sha256()
        for path,content in sorted(self.files.items()):
            digest.update(path.encode());digest.update(hashlib.sha256(content).digest())
        self.revision=digest.hexdigest()[:16]
        self.compressed={path:gzip.compress(content,compresslevel=6,mtime=0)
                         for path,content in self.files.items()
                         if len(content)>1024 and Path(path).suffix in {'.js','.css','.svg','.json'}}

    def gzip(self,path):
        if path.startswith('/assets/'):
            parts=path.split('/',3)
            if len(parts)!=4 or parts[2]!=self.revision:return None
            path='/'+parts[3]
        return self.compressed.get(path)

    def html(self, token):
        html=self.files['/index.html'].decode('utf-8').replace('__CLIENT_TOKEN__',token)
        html=html.replace('__ASSET_REVISION__',self.revision)
        return re.sub(r'((?:src|href)=")/([^"#]+)(")',
                      lambda m:m[1]+'/assets/'+self.revision+'/'+m[2]+m[3],html)

    def read(self,path):
        if path.startswith('/assets/'):
            parts=path.split('/',3)
            if len(parts)!=4 or parts[2]!=self.revision:return None
            path='/'+parts[3]
        return self.files.get(path)
