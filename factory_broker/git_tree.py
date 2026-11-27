"""Exact public Git objects; no checkout, filters, templates, or repository commands."""

from pathlib import Path

from .boundary import MAX_FILE, need, permitted, safe_path, sha, text_bytes


MAX_TREE = 512 * 1024
MAX_WORKSPACE = 32 * 1024 * 1024
GIT = '/usr/bin/git'
MODES = {'100644': 0o644, '100755': 0o755}


def utf8(data):
    if len(data) > MAX_FILE or b'\0' in data:
        return None
    try:
        return data.decode('utf-8')
    except UnicodeDecodeError:
        return None


class Repository:
    def __init__(self, config, root, commands):
        self.git_dir = root / 'objects.git'
        mirror = config['public_mirror']
        self.url = Path(mirror).as_uri() if mirror is not None else f"https://github.com/{config['target']['repo']}.git"
        self.env = {
            'GIT_ALLOW_PROTOCOL': 'file' if mirror is not None else 'https',
            'GIT_CONFIG_NOSYSTEM': '1', 'GIT_CONFIG_GLOBAL': '/dev/null', 'GIT_ATTR_NOSYSTEM': '1',
            'GIT_NO_REPLACE_OBJECTS': '1', 'GIT_NO_LAZY_FETCH': '1', 'GIT_TERMINAL_PROMPT': '0',
            'GIT_AUTHOR_NAME': 'SilentDeluge Factory', 'GIT_AUTHOR_EMAIL': 'factory@invalid',
            'GIT_COMMITTER_NAME': 'SilentDeluge Factory', 'GIT_COMMITTER_EMAIL': 'factory@invalid',
        }
        self.commands = commands
        self.git('init', '--quiet', '--bare', '--template=', '--object-format=sha1')

    def git(self, *args, **kwargs):
        return self.commands.run([
            GIT, f'--git-dir={self.git_dir}', '-c', 'core.hooksPath=/dev/null',
            '-c', 'core.attributesFile=/dev/null', '-c', 'credential.helper=',
            '-c', 'http.followRedirects=false', '-c', 'fetch.fsckObjects=true',
            '-c', 'fetch.writeCommitGraph=false', '-c', 'gc.auto=0', '-c', 'maintenance.auto=false',
            '-c', 'commit.gpgSign=false', *args,
        ], extra_env=self.env, **kwargs)

    def ref(self, branch):
        name = f'refs/heads/{branch}'
        result = self.git('ls-remote', '--refs', '--exit-code', '--', self.url, name, cap=1024, missing=2)
        if result is None:
            return None
        oid, tab, ref = result.decode('ascii').partition('\t')
        need(sha(oid) and tab and ref == name + '\n', 'unexpected public ref observation')
        return oid

    def fetch(self, oid):
        self.git('fetch', '--quiet', '--no-tags', '--no-recurse-submodules', '--depth=1', '--', self.url, oid,
                 timeout=120)
        return self.commit(oid)

    def commit(self, oid):
        kind = self.git('cat-file', '-t', oid, cap=32)
        need(kind == b'commit\n', 'requested object is not a commit')
        header, sep, _ = self.git('cat-file', 'commit', oid, cap=MAX_FILE).partition(b'\n\n')
        lines = header.split(b'\n')
        tree = lines[0].removeprefix(b'tree ').decode('ascii')
        need(sep and lines[0].startswith(b'tree ') and sha(tree), 'invalid commit object')
        parents = [line[7:].decode('ascii') for line in lines[1:] if line.startswith(b'parent ')]
        need(all(sha(parent) for parent in parents), 'invalid commit ancestry')
        return tree, parents

    def tree(self, oid):
        raw = self.git('ls-tree', '-rzt', '--full-tree', oid, cap=MAX_TREE)
        records = raw.split(b'\0')
        need(records.pop() == b'' and len(records) <= 4096, 'unsupported tree size')
        files = {}
        seen = set()
        for record in records:
            header, sep, name = record.partition(b'\t')
            mode, kind, blob = header.decode('ascii').split(' ')
            path = name.decode('ascii')
            need(sep and safe_path(path) and path not in seen and sha(blob), 'unsafe path in public tree')
            seen.add(path)
            if mode == '040000' and kind == 'tree':
                continue
            need(mode in MODES and kind == 'blob', 'unsupported file type in public tree')
            files[path] = mode, blob
        return files

    def blobs(self, oids, limit):
        if not oids:
            return {}
        unique = sorted(set(oids))
        body = ('\n'.join(unique) + '\n').encode('ascii')
        raw = self.git('cat-file', '--batch', body=body, cap=limit + len(unique) * 80)
        contents = {}
        offset = 0
        size = 0
        for oid in unique:
            end = raw.find(b'\n', offset, offset + 80)
            need(end != -1, 'invalid blob response')
            actual, kind, length = raw[offset:end].decode('ascii').split(' ')
            length = int(length)
            offset = end + 1
            size += length
            need(actual == oid and kind == 'blob' and length >= 0 and size <= limit
                 and raw[offset + length:offset + length + 1] == b'\n', 'invalid or oversized blob response')
            contents[oid] = raw[offset:offset + length]
            offset += length + 1
        need(offset == len(raw), 'unexpected blob response')
        return contents

    def context(self, tree, scopes):
        scoped = {path: oid for path, (_, oid) in tree.items() if permitted(path, scopes)}
        blobs = self.blobs(scoped.values(), MAX_TREE)
        return {path: text for path, oid in scoped.items() if (text := utf8(blobs[oid])) is not None}

    def candidate(self, base, original, context, replacements, key):
        need(isinstance(replacements, dict) and 0 < len(replacements) < 100, 'invalid replacement count')
        edits = {}
        for path, content in replacements.items():
            need(path in context and content != context[path], 'replacement is outside editable scope or unchanged')
            edits[path] = text_bytes(content)
        self.git('read-tree', base)
        updates = bytearray()
        for path, content in edits.items():
            oid = self.git('hash-object', '-w', '--stdin', body=content, cap=64).decode('ascii').strip()
            need(sha(oid), 'invalid replacement blob')
            updates.extend(f'{original[path][0]} {oid}\t{path}\0'.encode('ascii'))
        self.git('update-index', '-z', '--index-info', body=updates)
        tree = self.git('write-tree', cap=64).decode('ascii').strip()
        need(sha(tree), 'invalid candidate tree')
        oid = self.git('commit-tree', tree, '-p', base, body=f'Factory candidate {key}\n'.encode('ascii'),
                       cap=64).decode('ascii').strip()
        need(sha(oid), 'invalid candidate commit')
        return oid

    def changes(self, before, after, scopes):
        changed = {path for path in before.keys() | after.keys() if before.get(path) != after.get(path)}
        need(0 < len(changed) < 100, 'invalid candidate change count')
        for path in changed:
            need(permitted(path, scopes) and path in before and path in after
                 and before[path][0] == after[path][0], 'candidate exceeds replacement scope')
        blobs = self.blobs((tree[path][1] for path in changed for tree in (before, after)), MAX_TREE)
        result = {}
        for path in sorted(changed):
            old, new = (utf8(blobs[tree[path][1]]) for tree in (before, after))
            need(old is not None and new is not None, 'candidate replacement is not UTF-8 text')
            result[path] = {'before': old, 'after': new}
        return result

    def pack(self, base, candidate):
        return self.git('pack-objects', '--stdout', '--revs', body=f'{candidate}\n^{base}\n'.encode('ascii'),
                        cap=2 * MAX_TREE)

    def workspace(self, tree, root):
        blobs = self.blobs((oid for _, oid in tree.values()), MAX_WORKSPACE)
        need(sum(len(blobs[oid]) for _, oid in tree.values()) <= MAX_WORKSPACE, 'workspace exceeds size limit')
        root.mkdir(mode=0o700)
        for path, (mode, oid) in tree.items():
            dest = root / path
            dest.parent.mkdir(parents=True, exist_ok=True)
            with dest.open('xb') as stream:
                stream.write(blobs[oid])
            dest.chmod(MODES[mode])
