from common.director import director_review


def test_director_review_returns_the_review_dict():
    review = {"approved": False}
    assert director_review({"meta": {"director": {"review": review}}}) == review


def test_director_review_returns_none_when_absent():
    assert director_review({}) is None
    assert director_review({"meta": {}}) is None
    assert director_review({"meta": {"director": {}}}) is None
    assert director_review({"meta": {"director": {"review": "no"}}}) is None
    assert director_review({"meta": {"director": {"review": None}}}) is None


def test_director_review_returns_none_for_a_non_dict_document():
    assert director_review(None) is None
    assert director_review([]) is None
    assert director_review("edit") is None
