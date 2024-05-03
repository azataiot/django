The `--install-option` is not supported when installing packages with `pip`'s `pyproject.toml` (PEP 517/518) build system, which is the case for `pylibmc`.

As an alternative, you can try setting the `CFLAGS` and `LDFLAGS` environment variables to include the paths to the `libmemcached` include and lib directories before running `pip install`. Here's how you can do it:

```bash
export CFLAGS="-I/opt/homebrew/Cellar/libmemcached/1.0.18_2/include"
export LDFLAGS="-L/opt/homebrew/Cellar/libmemcached/1.0.18_2/lib"
pip install pylibmc
```

This will tell the compiler where to find the `libmemcached` headers and libraries. Replace the paths with the actual paths on your system. You can find these paths by running `brew info libmemcached`.