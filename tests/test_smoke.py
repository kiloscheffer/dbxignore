def test_package_importable() -> None:
    import dbxignore

    assert dbxignore.__version__
