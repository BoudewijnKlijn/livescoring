"""Accounts voor de wedstrijdleiding: registreren, bevestigen, inloggen en afscherming.

De kern van dit bestand is de laatste groep: een wedstrijdleider hoort alleen bij zijn eigen
wedstrijd te kunnen, ook als hij het id van een ander raadt.
"""

from __future__ import annotations

import re

import pytest
from sqlalchemy import select, text

from app import account
from app.account import MAX_EMAIL, MAX_WACHTWOORD, hash_password, verify_password
from app.config import settings
from app.models import GEEN_WACHTWOORD, AuditLog, Competition, User, create_all
from tests.helpers import entry_van, login_admin, vul_kaart

WACHTWOORD = "eengoedwachtwoord"


@pytest.fixture
def mails(monkeypatch):
    """Zet mailen aan en vang het versturen af, zodat er niets de deur uit gaat."""
    monkeypatch.setattr(settings, "brevo_api_key", "test-sleutel")
    monkeypatch.setattr(settings, "mail_from", "wedstrijd@club.nl")
    monkeypatch.setattr(settings, "base_url", "https://scoring.club.nl")
    verstuurd: list[dict] = []
    monkeypatch.setattr(account, "verstuur", verstuurd.append)
    return verstuurd


@pytest.fixture
def registreren(client, mails):
    """Registreer een account en geef het bevestigingstoken uit de mail terug."""

    def _registreren(email: str = "leider@club.nl", wachtwoord: str = WACHTWOORD) -> str:
        client.cookies.clear()
        antwoord = client.post(
            "/admin/registreren", data={"email": email, "wachtwoord": wachtwoord}
        )
        assert antwoord.status_code == 200, antwoord.text
        return re.search(r"/admin/bevestigen/(\S+)", mails[-1]["textContent"]).group(1)

    return _registreren


@pytest.fixture
def ingelogd(client, registreren):
    """Een bevestigd account, ingelogd. Geeft de client terug."""

    def _ingelogd(email: str = "leider@club.nl"):
        token = registreren(email)
        assert client.get(f"/admin/bevestigen/{token}", follow_redirects=False).status_code == 303
        client.cookies.clear()
        antwoord = client.post(
            "/admin/login",
            data={"email": email, "wachtwoord": WACHTWOORD},
            follow_redirects=False,
        )
        assert antwoord.status_code == 303
        return client

    return _ingelogd


def test_wachtwoord_wordt_niet_leesbaar_opgeslagen(db, registreren):
    """Alleen een hash gaat de database in, en die is per account anders."""
    registreren("een@club.nl")
    registreren("twee@club.nl")

    db.expire_all()
    hashes = [u.password_hash for u in db.scalars(select(User).order_by(User.id))]

    assert len(hashes) == 2
    assert all(WACHTWOORD not in h for h in hashes)
    assert hashes[0] != hashes[1], "elk account krijgt zijn eigen salt"
    assert all(verify_password(WACHTWOORD, h) for h in hashes)
    assert not verify_password("iets anders", hashes[0])


def test_hash_is_niet_te_vergelijken_met_een_kapotte_waarde():
    """Een onvolledige hash uit de database mag geen exception geven, alleen 'nee'."""
    assert not verify_password(WACHTWOORD, "")
    assert not verify_password(WACHTWOORD, "rommel")
    assert verify_password(WACHTWOORD, hash_password(WACHTWOORD))


def test_bevestigingsmail_bevat_de_link(db, registreren, mails):
    """De mail gaat naar het opgegeven adres en bevat de bevestigingslink."""
    token = registreren("leider@club.nl")

    bericht = mails[-1]
    assert bericht["to"] == [{"email": "leider@club.nl", "name": "leider@club.nl"}]
    assert f"https://scoring.club.nl/admin/bevestigen/{token}" in bericht["textContent"]
    assert f"/admin/bevestigen/{token}" in bericht["htmlContent"]


def test_alleen_de_hash_van_het_bevestigingstoken_staat_in_de_database(db, registreren):
    """Net als bij de spelerslinks bewaart de database het token zelf niet."""
    token = registreren("leider@club.nl")

    db.expire_all()
    gebruiker = db.scalar(select(User))
    assert gebruiker.confirm_token_hash is not None
    assert token not in gebruiker.confirm_token_hash


def test_inloggen_kan_pas_na_bevestigen(db, client, registreren):
    """Zonder bevestigd adres geen toegang, ook niet met het goede wachtwoord."""
    registreren("leider@club.nl")
    client.cookies.clear()

    antwoord = client.post(
        "/admin/login", data={"email": "leider@club.nl", "wachtwoord": WACHTWOORD}
    )

    assert antwoord.status_code == 401
    assert "bevestig" in antwoord.text.lower()
    assert client.get("/admin", follow_redirects=False).status_code == 303


def test_bevestigen_geeft_toegang(db, client, registreren):
    """Na het volgen van de link kan de wedstrijdleider inloggen en een wedstrijd maken."""
    token = registreren("leider@club.nl")

    assert client.get(f"/admin/bevestigen/{token}", follow_redirects=False).status_code == 303
    db.expire_all()
    assert db.scalar(select(User)).confirmed_at is not None

    client.cookies.clear()
    antwoord = client.post(
        "/admin/login",
        data={"email": "leider@club.nl", "wachtwoord": WACHTWOORD},
        follow_redirects=False,
    )

    assert antwoord.status_code == 303
    assert client.get("/admin").status_code == 200


def test_bevestigingslink_werkt_maar_een_keer(db, client, registreren):
    """Een gebruikte link is dood, zodat hij niet uit een oude mailbox terugkomt."""
    token = registreren("leider@club.nl")
    client.get(f"/admin/bevestigen/{token}", follow_redirects=False)

    antwoord = client.get(f"/admin/bevestigen/{token}", follow_redirects=False)

    assert antwoord.status_code == 404


def test_verzonnen_bevestigingslink_bevestigt_niets(db, client, registreren):
    """Een token dat niet bestaat mag geen account openzetten."""
    registreren("leider@club.nl")

    assert client.get("/admin/bevestigen/verzonnen", follow_redirects=False).status_code == 404
    db.expire_all()
    assert db.scalar(select(User)).confirmed_at is None


def test_verkeerd_wachtwoord_geeft_geen_toegang(db, client, ingelogd):
    """Het goede adres met het verkeerde wachtwoord komt er niet in."""
    ingelogd("leider@club.nl")
    client.cookies.clear()

    antwoord = client.post(
        "/admin/login", data={"email": "leider@club.nl", "wachtwoord": "gokje12345"}
    )

    assert antwoord.status_code == 401
    assert client.get("/admin", follow_redirects=False).status_code == 303


def test_onbekend_adres_geeft_geen_toegang(db, client):
    """Een adres zonder account levert dezelfde melding op als een fout wachtwoord."""
    antwoord = client.post(
        "/admin/login", data={"email": "niemand@club.nl", "wachtwoord": WACHTWOORD}
    )

    assert antwoord.status_code == 401


def test_een_wachtwoord_van_een_teken_mag(db, client, registreren):
    """Hoe sterk het wachtwoord is, is aan de eigenaar. De app stelt geen eisen."""
    token = registreren("leider@club.nl", "x")
    assert client.get(f"/admin/bevestigen/{token}", follow_redirects=False).status_code == 303
    client.cookies.clear()

    antwoord = client.post(
        "/admin/login",
        data={"email": "leider@club.nl", "wachtwoord": "x"},
        follow_redirects=False,
    )

    assert antwoord.status_code == 303


def test_leeg_wachtwoord_wordt_geweigerd(db, client, mails):
    """Een leeg veld is een vergissing, geen keuze."""
    antwoord = client.post(
        "/admin/registreren", data={"email": "leider@club.nl", "wachtwoord": ""}
    )

    assert antwoord.status_code == 422
    db.expire_all()
    assert db.scalars(select(User)).all() == []
    assert mails == []


def test_te_lange_invoer_komt_de_database_niet_in(db, client, mails):
    """Een veld van een megabyte hoort te stuiten voordat het wordt opgeslagen of gehasht."""
    lang_adres = "a" * MAX_EMAIL + "@club.nl"
    lang_wachtwoord = "x" * (MAX_WACHTWOORD + 1)

    for gegevens in (
        {"email": lang_adres, "wachtwoord": WACHTWOORD},
        {"email": "leider@club.nl", "wachtwoord": lang_wachtwoord},
    ):
        assert client.post("/admin/registreren", data=gegevens).status_code == 422

    db.expire_all()
    assert db.scalars(select(User)).all() == []
    assert mails == []


def test_wachtwoord_op_de_grens_mag(db, client, registreren):
    """Precies de maximale lengte hoort er nog in te passen."""
    token = registreren("leider@club.nl", "x" * MAX_WACHTWOORD)

    assert client.get(f"/admin/bevestigen/{token}", follow_redirects=False).status_code == 303


def test_adres_zonder_apenstaartje_wordt_geweigerd(db, client, mails):
    """Zonder geldig adres komt de bevestigingsmail nergens aan."""
    antwoord = client.post(
        "/admin/registreren", data={"email": "geen adres", "wachtwoord": WACHTWOORD}
    )

    assert antwoord.status_code == 422
    db.expire_all()
    assert db.scalars(select(User)).all() == []
    assert mails == []


def test_bevestigd_adres_kan_niet_nog_een_keer(db, client, ingelogd, mails):
    """Een tweede registratie op een bevestigd adres verandert het wachtwoord niet."""
    ingelogd("leider@club.nl")
    db.expire_all()
    voor = db.scalar(select(User)).password_hash
    aantal_mails = len(mails)

    antwoord = client.post(
        "/admin/registreren", data={"email": "leider@club.nl", "wachtwoord": "anderwachtwoord"}
    )

    assert antwoord.status_code == 422
    assert "al een account" in antwoord.text
    db.expire_all()
    assert db.scalar(select(User)).password_hash == voor
    assert len(mails) == aantal_mails, "en er gaat geen mail naar de eigenaar van het adres"


def test_onbevestigd_adres_krijgt_een_nieuwe_link(db, client, registreren, mails):
    """Kwam de eerste mail niet aan, dan levert opnieuw registreren een verse link op."""
    eerste = registreren("leider@club.nl")

    tweede = registreren("leider@club.nl")

    assert tweede != eerste
    db.expire_all()
    assert len(db.scalars(select(User)).all()) == 1
    assert client.get(f"/admin/bevestigen/{eerste}", follow_redirects=False).status_code == 404
    assert client.get(f"/admin/bevestigen/{tweede}", follow_redirects=False).status_code == 303


def test_adres_is_hoofdletterongevoelig(db, client, registreren):
    """Wie zich met een hoofdletter aanmeldt logt in zonder erover na te denken."""
    token = registreren("Leider@Club.nl")
    client.get(f"/admin/bevestigen/{token}", follow_redirects=False)
    client.cookies.clear()

    antwoord = client.post(
        "/admin/login",
        data={"email": " leider@club.nl ", "wachtwoord": WACHTWOORD},
        follow_redirects=False,
    )

    assert antwoord.status_code == 303


def test_zonder_account_is_er_geen_ingang(db, client, wedstrijd):
    """Het losse beheerderswachtwoord bestaat niet meer: zonder account kom je nergens."""
    competition, _ = wedstrijd

    antwoord = client.post("/admin/login", data={"wachtwoord": "testwachtwoord"})

    assert antwoord.status_code == 401
    assert client.get("/admin", follow_redirects=False).status_code == 303
    assert client.get(f"/admin/c/{competition.id}", follow_redirects=False).status_code == 303


def test_eigen_wedstrijd_hoort_bij_het_account(db, client, ingelogd):
    """Wat een wedstrijdleider aanmaakt staat op zijn naam."""
    ingelogd("leider@club.nl")

    client.post("/admin/competition", data={"naam": "Clubkampioenschap"}, follow_redirects=False)

    db.expire_all()
    gebruiker = db.scalar(select(User))
    competition = db.scalar(select(Competition))
    assert competition.user_id == gebruiker.id


def test_wedstrijd_van_een_ander_is_onzichtbaar(db, client, ingelogd):
    """Het overzicht toont alleen de eigen wedstrijden."""
    ingelogd("een@club.nl")
    client.post("/admin/competition", data={"naam": "Wedstrijd van een"})
    ingelogd("twee@club.nl")
    client.post("/admin/competition", data={"naam": "Wedstrijd van twee"})

    overzicht = client.get("/admin").text

    assert "Wedstrijd van twee" in overzicht
    assert "Wedstrijd van een" not in overzicht


def test_wedstrijd_van_een_ander_is_niet_te_openen(db, client, ingelogd, wedstrijd):
    """Ook met het juiste id komt een wedstrijdleider niet bij andermans wedstrijd."""
    van_een_ander, _ = wedstrijd
    jan = entry_van(db, "Jan")
    rnd = van_een_ander.rounds[0]
    ingelogd("twee@club.nl")

    for pad in (
        f"/admin/c/{van_een_ander.id}",
        f"/admin/c/{van_een_ander.id}/export.csv",
    ):
        assert client.get(pad).status_code == 404, pad

    for pad, data in (
        (f"/admin/c/{van_een_ander.id}/import", {"csv_tekst": ""}),
        (f"/admin/c/{van_een_ander.id}/verbergen", {}),
        (f"/admin/c/{van_een_ander.id}/wissen", {"verwacht": "ABCD", "code": "ABCD"}),
        (f"/admin/c/{van_een_ander.id}/rotate", {"scope": "competition"}),
        (f"/admin/round/{rnd.id}/pars", {"pars": "3 " * 18}),
        (f"/admin/entry/{jan.id}/status", {"status": "dq"}),
        (f"/admin/entry/{jan.id}/unlock", {}),
        (f"/admin/entry/{jan.id}/reset", {"verwacht": "ABCD", "code": "ABCD"}),
        (f"/admin/entry/{jan.id}/score", {"hole": 1, "strokes": 9, "reden": "test"}),
    ):
        assert client.post(pad, data=data).status_code == 404, pad

    db.expire_all()
    assert entry_van(db, "Jan").status == "ok"
    assert entry_van(db, "Jan").scores == []
    assert db.get(Competition, van_een_ander.id).status == "live"
    assert db.get(Competition, van_een_ander.id).rounds[0].pars != [3] * 18


def test_link_vervangen_blijft_binnen_de_eigen_wedstrijd(db, client, ingelogd, wedstrijd):
    """Een speler uit een andere wedstrijd meesturen levert hem geen nieuwe link op."""
    _, tokens = wedstrijd
    jan = entry_van(db, "Jan")
    client_van_twee = ingelogd("twee@club.nl")
    client_van_twee.post("/admin/competition", data={"naam": "Eigen wedstrijd"})
    db.expire_all()
    eigen = db.scalar(select(Competition).where(Competition.name == "Eigen wedstrijd"))

    antwoord = client.post(
        f"/admin/c/{eigen.id}/rotate",
        data={"scope": "entry", "entry_id": jan.id, "verwacht": "ABCD", "code": "ABCD"},
    )

    assert antwoord.status_code in (400, 404)
    client.cookies.clear()
    assert client.get(f"/t/{tokens['Jan']}", follow_redirects=False).status_code == 303


def test_uitloggen_sluit_de_deur(db, client, ingelogd):
    """Na uitloggen is het overzicht weer achter het inlogscherm."""
    ingelogd("leider@club.nl")
    client.post("/admin/logout", follow_redirects=False)

    assert client.get("/admin", follow_redirects=False).status_code == 303


def test_auditlog_toont_alleen_de_eigen_wedstrijd(db, client, ingelogd, wedstrijd):
    """De audit log is per wedstrijdleider: andermans correcties horen er niet in te staan."""
    login_admin(client)
    jan = entry_van(db, "Jan")
    client.post(
        f"/admin/entry/{jan.id}/status", data={"status": "dq", "reden": "geheime reden"}
    )

    ingelogd("twee@club.nl")
    client.post("/admin/competition", data={"naam": "Eigen wedstrijd"})
    auditlog = client.get("/admin/audit.csv")

    assert auditlog.status_code == 200
    regels = auditlog.text.strip().splitlines()
    assert len(regels) == 2, "de kop en de eigen wedstrijd, verder niets"
    assert "Eigen wedstrijd" in regels[1]
    assert "geheime reden" not in auditlog.text


def test_spelersacties_komen_bij_de_eigenaar_terecht(db, client, wedstrijd):
    """Wat een speler doet hoort in de audit log van de wedstrijdleider van die wedstrijd.

    Niet omdat de regel hem noemt -- hij deed het niet -- maar omdat hij de wedstrijd heeft.
    """
    competition, _ = wedstrijd
    vul_kaart(db, entry_van(db, "Jan"), holes=1)

    db.expire_all()
    regels = db.scalars(select(AuditLog).where(AuditLog.action == "score")).all()
    assert regels != []
    assert all(r.competition_id == competition.id for r in regels)

    login_admin(client)
    assert "score" in client.get("/admin/audit.csv").text


def test_oude_wedstrijd_krijgt_de_eigenaar_uit_de_instelling(db, monkeypatch):
    """De database van voor de accounts: elke wedstrijd zonder eigenaar krijgt er een.

    Dit is precies wat er bij het eerste opstarten in productie gebeurt, dus het wordt hier
    ook precies zo nagespeeld: kolom weer leeg toestaan, een wedstrijd zonder eigenaar
    erin, en dan opstarten.
    """
    monkeypatch.setattr(settings, "owner_email", "Hans@Hste.nl")
    db.execute(text("alter table competition alter column user_id drop not null"))
    db.execute(
        text(
            "insert into competition (name, status, leaderboard_slug, created_at) "
            "values ('Oude wedstrijd', 'live', 'oud', now())"
        )
    )
    db.commit()

    create_all()

    db.expire_all()
    eigenaar = db.scalar(select(User))
    competition = db.scalar(select(Competition))
    assert eigenaar.email == "hans@hste.nl", "genormaliseerd, net als bij het aanmelden"
    assert competition.user_id == eigenaar.id
    assert eigenaar.confirmed_at is None, "de eigenaar moet het account nog opeisen"
    assert eigenaar.password_hash == GEEN_WACHTWOORD


def test_de_eigenaar_van_de_migratie_eist_zijn_account_op(db, client, registreren, monkeypatch):
    """Het aangemaakte account heeft geen wachtwoord; de eigenaar zet er zelf een op."""
    monkeypatch.setattr(settings, "owner_email", "hans@hste.nl")
    db.execute(text("alter table competition alter column user_id drop not null"))
    db.execute(
        text(
            "insert into competition (name, status, leaderboard_slug, created_at) "
            "values ('Oude wedstrijd', 'live', 'oud', now())"
        )
    )
    db.commit()
    create_all()

    assert (
        client.post(
            "/admin/login", data={"email": "hans@hste.nl", "wachtwoord": GEEN_WACHTWOORD}
        ).status_code
        == 401
    ), "het onbruikbare wachtwoord is geen wachtwoord"

    token = registreren("hans@hste.nl", "zelfgekozen")
    assert client.get(f"/admin/bevestigen/{token}", follow_redirects=False).status_code == 303
    client.cookies.clear()
    login_admin(client, "hans@hste.nl", "zelfgekozen")

    assert "Oude wedstrijd" in client.get("/admin").text


def test_zonder_owner_email_start_de_app_niet(db, monkeypatch):
    """Liever een duidelijke fout dan een wedstrijd die stilzwijgend van niemand is."""
    monkeypatch.setattr(settings, "owner_email", "")
    db.execute(text("alter table competition alter column user_id drop not null"))
    db.execute(
        text(
            "insert into competition (name, status, leaderboard_slug, created_at) "
            "values ('Oude wedstrijd', 'live', 'oud', now())"
        )
    )
    db.commit()

    with pytest.raises(RuntimeError, match="OWNER_EMAIL"):
        create_all()
