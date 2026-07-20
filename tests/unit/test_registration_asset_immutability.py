from devnous.copa_telmex.models import RegistrationReviewSession


def test_review_session_does_not_cascade_delete_immutable_assets():
    relationship = RegistrationReviewSession.assets.property

    assert "delete" not in relationship.cascade
    assert "delete-orphan" not in relationship.cascade
    assert relationship.passive_deletes == "all"
