import pickle

from formcast.errors import FormcastError, OpError


def test_op_error_pickle_round_trip_keeps_op_and_reason():
    err = OpError({"op": "move", "id": "f1"}, "unknown field id 'f1'")
    back = pickle.loads(pickle.dumps(err))
    assert isinstance(back, OpError) and isinstance(back, FormcastError)
    assert back.op == {"op": "move", "id": "f1"}
    assert back.reason == "unknown field id 'f1'"
    assert str(back) == str(err) == "unknown field id 'f1'"
