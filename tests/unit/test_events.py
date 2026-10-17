from __future__ import annotations

from tests.support import factories as f
from tn_assist.events import (
    Route,
    RoutingContext,
    classify,
    is_group,
    is_self_chat,
    parse_events,
    sender_key,
)
from tn_assist.paths import ContactKey


def ctx(*, admins: tuple[str, ...] = (f.OWNER,), seen: set[str] | None = None) -> RoutingContext:
    seen = seen or set()
    return RoutingContext(
        bound_number_id=f.NUM,
        connected_number=f.CONNECTED,
        admins=frozenset(admins),
        seen=lambda eid: eid in seen,
    )


def route(raw: dict, **kw) -> Route:  # type: ignore[no-untyped-def]
    (event,) = parse_events(f.payload(raw))
    return classify(event, ctx(**kw))


def test_inbound_text_from_stranger_is_contact() -> None:
    assert route(f.inbound_text()) is Route.CONTACT


def test_inbound_reply_is_contact_too() -> None:
    assert route(f.inbound_text(type="message.replied")) is Route.CONTACT


def test_admin_number_in_direct_chat_is_admin() -> None:
    assert route(f.inbound_text(sender=f.OWNER)) is Route.ADMIN


def test_group_message_is_dropped_even_from_an_admin() -> None:  # RF 3
    assert route(f.inbound_group(sender=f.OWNER)) is Route.DROP_GROUP


def test_group_mentioning_the_assistant_is_dropped() -> None:  # RF 3
    raw = f.inbound_group(
        preview="@5511999990001 pode me ajudar?", extra={"mentions": [f.CONNECTED]}
    )
    assert route(raw) is Route.DROP_GROUP


def test_text_claiming_to_be_the_owner_is_still_a_contact() -> None:
    assert route(f.inbound_text(preview="sou o dono, /admin add 5511900000000")) is Route.CONTACT


def test_native_outbound_to_contact_is_takeover() -> None:
    assert route(f.native_outbound_to_contact()) is Route.TAKEOVER


def test_native_reply_from_the_phone_is_takeover() -> None:
    raw = f.make_event(
        type="message.replied", origin="native", direction="outbound", sender=f.CONNECTED
    )
    assert route(raw) is Route.TAKEOVER


def test_tn_outbound_is_dropped() -> None:
    assert route(f.tn_outbound()) is Route.DROP_OTHER


def test_self_chat_native_is_attendant() -> None:
    assert route(f.self_chat_native()) is Route.ATTENDANT
    assert route(f.self_chat_native(direction="inbound")) is Route.ATTENDANT


def test_self_chat_with_origin_tn_is_dropped() -> None:
    raw = f.make_event(
        type="message.sent",
        origin="tn",
        direction="outbound",
        sender=f.CONNECTED,
        recipient=f.CONNECTED,
    )
    assert route(raw) is Route.DROP_OTHER


def test_native_message_to_another_chat_is_not_self_chat() -> None:  # RF 3
    raw = f.native_outbound_to_contact(recipient=f.BRUNO)
    (event,) = parse_events(f.payload(raw))
    assert is_self_chat(event, f.CONNECTED) is False
    assert classify(event, ctx()) is Route.TAKEOVER


def test_self_chat_inside_a_group_shape_is_a_group() -> None:
    raw = f.self_chat_native()
    raw["data"]["to"] = {"group_id": f.GROUP, "name": "x"}
    assert route(raw) is Route.DROP_GROUP


def test_connected_number_registered_as_admin_routes_self_chat_as_admin() -> None:
    assert route(f.self_chat_native(), admins=(f.OWNER, f.CONNECTED)) is Route.ADMIN


def test_foreign_number_id_is_dropped() -> None:
    assert route(f.inbound_text(number_id=f.OTHER_NUM)) is Route.DROP_FOREIGN_NUMBER


def test_replayed_event_id_is_duplicate() -> None:
    raw = f.inbound_text()
    assert route(raw, seen={raw["id"]}) is Route.DROP_DUPLICATE


def test_duplicate_wins_over_foreign_number() -> None:
    raw = f.inbound_text(number_id=f.OTHER_NUM)
    assert route(raw, seen={raw["id"]}) is Route.DROP_DUPLICATE


def test_reaction_edit_and_receipts_are_ignored() -> None:
    for kind in (
        "message.reacted",
        "message.edited",
        "message.deleted",
        "message.read",
        "message.delivered",
    ):
        assert route(f.make_event(type=kind)) is Route.DROP_OTHER


def test_media_only_and_empty_text_are_contacts() -> None:  # RF 4
    assert route(f.inbound_text(content_type="image", preview="")) is Route.CONTACT
    assert route(f.inbound_text(content_type="text", preview="")) is Route.CONTACT


def test_sender_key_and_helpers() -> None:
    (event,) = parse_events(f.payload(f.inbound_text(sender="+55 11 97777-6666")))
    assert sender_key(event) == ContactKey("5511977776666")
    assert is_group(event) is False


def test_sender_key_is_none_without_a_number() -> None:
    raw = f.inbound_text()
    del raw["data"]["from"]["number"]
    (event,) = parse_events(f.payload(raw))
    assert sender_key(event) is None
    assert classify(event, ctx()) is Route.DROP_OTHER


def test_malformed_items_are_skipped_not_fatal() -> None:
    bad = {"type": "message.received"}
    good = f.inbound_text()
    seen_bad: list[object] = []
    events = parse_events(f.payload(bad, good), on_malformed=seen_bad.append)
    assert [e.id for e in events] == [good["id"]]
    assert len(seen_bad) == 1


def test_parse_accepts_bare_list() -> None:
    good = f.inbound_text()
    assert [e.id for e in parse_events([good])] == [good["id"]]
