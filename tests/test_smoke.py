def test_import():
    import formcast
    from formcast import errors

    assert formcast is not None
    assert errors.OpError(op={}, reason="x").reason == "x"
