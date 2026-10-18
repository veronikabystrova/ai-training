def test_package_importable() -> None:
    import app

    assert app.__name__ == "app"
