# source: https://gist.github.com/stavxyz/50b918092ca7e02429caeca210b1c2db
# Copyright 2019 Sam Stavinoha

import hashlib


def _tob(_string, enc='utf8'):
    if isinstance(_string, str):
        return _string.encode(enc)
    return b'' if _string is None else bytes(_string)


def args_hash(*args, **kw):
    """Calculate a hash value from string args and kwargs.
    Given the same positional arguments and keyword
    arguments, this function will produce the same
    hash key.
    """
    items = list(args) + [i for t in sorted(kw.items()) for i in t]
    items = ('__NoneType__' if _i is None else _i
             for _i in items)
    # All items must be strings
    args_string = '|'.join(items)
    return hashlib.sha1(_tob(args_string)).hexdigest()
