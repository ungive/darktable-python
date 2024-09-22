import sys
import hashlib
import sqlite3

import simple_cache


def fullname(o):
    klass = o.__class__
    return o.__module__ + ':' + '.'.join([
        klass.__module__,
        klass.__qualname__,
        o.__name__
    ])


def filehash(filepath):
    sha1 = hashlib.sha1()
    with open(filepath, 'rb') as f:
        sha1.update(f.read())
    return sha1.hexdigest()


def readonly_sqlite_connection(db_path):
    con = sqlite3.connect(f'file:{db_path}?mode=ro', uri=True)
    con.row_factory = sqlite3.Row
    return con


class Cache:
    def __init__(self, cache_filepath, *, prefix=''):
        self.cache_filepath = cache_filepath
        self.key_prefix = prefix

    def save(self, key, value):
        return simple_cache.save_key(self.cache_filepath, self.key_prefix + key, value, sys.maxsize)

    def load(self, key):
        return simple_cache.load_key(self.cache_filepath, self.key_prefix + key)

    def store(self, key):
        return self.save(key, True)

    def contains(self, key):
        return self.load(key) != None

    def delete(self, key):
        cache = simple_cache.read_cache(self.cache_filepath)
        actual_key = self.key_prefix + key
        if actual_key in cache:
            del cache[actual_key]
        simple_cache.write_cache(self.cache_filepath, cache)

    def prune(self):
        cache = simple_cache.read_cache(self.cache_filepath)
        for key in list(cache.keys()):
            if key.startswith(self.key_prefix):
                del cache[key]
        simple_cache.write_cache(self.cache_filepath, cache)

    def update(self, dictionary):
        cache = simple_cache.read_cache(self.cache_filepath)
        for key, value in dictionary.items():
            cache[self.key_prefix + key] = (sys.maxsize, value)
        simple_cache.write_cache(self.cache_filepath, cache)

    def replace(self, dictionary):
        self.prune()
        self.update(dictionary)

    def items(self, *, has_value=None):
        cache = simple_cache.read_cache(self.cache_filepath)
        return [
            (key.removeprefix(self.key_prefix), value)
            for key, (_, value) in cache.items()
            if key.startswith(self.key_prefix)
            and (has_value is None or value == has_value)
        ]

    def keys(self, *, has_value=None):
        return [k for k, v in self.items(has_value=has_value)]
