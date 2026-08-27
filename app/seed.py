"""Demo-wedstrijd aanmaken om de flow zelf te kunnen doorlopen.

    uv run python -m app.seed --demo            vier spelers, twee ronden, lege kaarten
    uv run python -m app.seed --demo --scores   zestien spelers met een ingevulde stand
"""

from __future__ import annotations

import argparse
import random

from sqlalchemy import select

from app.config import settings
from app.importer import create_competition, import_csv
from app.models import DEFAULT_PARS, HOLES, Entry, Round, SessionLocal, create_all
from app.scoring import build_card, set_score, sign_card

DEMO_CSV = """naam,email,ronde,flight,starthole,marker
Jan de Vries,jan@example.nl,1,A,1,Piet Bakker
Piet Bakker,piet@example.nl,1,A,1,Jan de Vries
Anne Jansen,anne@example.nl,1,B,10,Kees Smit
Kees Smit,kees@example.nl,1,B,10,Anne Jansen
Jan de Vries,jan@example.nl,2,A,1,Anne Jansen
Anne Jansen,anne@example.nl,2,A,1,Jan de Vries
Piet Bakker,piet@example.nl,2,B,10,Kees Smit
Kees Smit,kees@example.nl,2,B,10,Piet Bakker
"""

# Zestien spelers in vier flights, twee ronden. Elke flight is een kring: A markt B, B markt
# C, C markt D, D markt A. Ronde 2 loopt met een andere indeling, zoals op een echte dag.
NAMEN = [
    "Jan de Vries", "Piet Bakker", "Anne Jansen", "Kees Smit",
    "Marieke Vos", "Ruud Hendriks", "Sanne de Boer", "Tom Willems",
    "Lotte Peters", "Bram van Dijk", "Fenna Mulder", "Joost Kramer",
    "Iris Bosman", "Daan Verhoeven", "Nora Schouten", "Wim Dekker",
]


def _kring_csv(ronde: int, volgorde: list[str], starthole: dict[str, int]) -> str:
    """Zet zestien namen in vier flights van vier, elk als kring van markers."""
    regels = []
    for i in range(0, len(volgorde), 4):
        groep = volgorde[i : i + 4]
        flight = "ABCD"[i // 4]
        for j, naam in enumerate(groep):
            marker = groep[(j + 1) % len(groep)]
            regels.append(
                f"{naam},{naam.split()[0].lower()}@example.nl,{ronde},"
                f"{flight},{starthole[flight]},{marker}"
            )
    return "\n".join(regels)


def showcase_csv() -> str:
    """Twee ronden met een andere flightindeling, net als op een echte wedstrijddag."""
    kop = "naam,email,ronde,flight,starthole,marker"
    ronde1 = _kring_csv(1, NAMEN, {"A": 1, "B": 1, "C": 10, "D": 10})
    # Na ronde 1 loopt de leider met de leider: de volgorde draait om.
    ronde2 = _kring_csv(2, NAMEN[::-1], {"A": 1, "B": 1, "C": 10, "D": 10})
    return f"{kop}\n{ronde1}\n{ronde2}\n"


def _speel(db, entry: Entry, pars: list[int], holes: int, vorm: int, kans: random.Random) -> None:
    """Speel `holes` holes. `vorm` schuift de verdeling: lager is een betere ronde."""
    for hole in range(1, holes + 1):
        par = pars[hole - 1]
        rol = kans.random() + vorm * 0.08
        if rol < 0.03 and par == 5:
            slagen = par - 2  # een eagle valt vrijwel alleen op een par 5
        elif rol < 0.20:
            slagen = par - 1
        elif rol < 0.62:
            slagen = par
        elif rol < 0.85:
            slagen = par + 1
        else:
            slagen = par + 2
        slagen = max(1, slagen)
        set_score(db, entry, entry, hole, "self", slagen)
        set_score(db, entry.marker, entry, hole, "marker", slagen)


def vul_scores(db, competition) -> None:
    """Ronde 1 helemaal uitgespeeld, ronde 2 halverwege: dat is het bord in vol bedrijf."""
    kans = random.Random(7)
    rondes = select(Round).where(Round.competition_id == competition.id)
    for rnd in db.scalars(rondes.order_by(Round.no)):
        entries = sorted(rnd.entries, key=lambda e: e.player.name)
        pars = rnd.pars or DEFAULT_PARS
        for i, entry in enumerate(entries):
            if rnd.no == 1:
                _speel(db, entry, pars, HOLES, i % 5, kans)
            else:
                # Onderweg: de een is bijna klaar, de ander moet nog beginnen.
                _speel(db, entry, pars, [18, 14, 11, 9, 7, 5, 3, 0][i % 8], i % 5, kans)

    # Eén speler valt uit en één hole blijft betwist: allebei komen ze op het bord terug.
    ronde1 = db.scalar(rondes.where(Round.no == 1))
    op_naam = sorted(ronde1.entries, key=lambda e: e.player.name)
    op_naam[-1].status = "wd"
    set_score(db, op_naam[2].marker, op_naam[2], 7, "marker", 9)
    db.commit()

    # De meeste kaarten van ronde 1 zijn getekend; twee blijven open, zoals altijd.
    for entry in op_naam[:-3]:
        card = build_card(entry)
        if card.signable:
            sign_card(db, entry)


def main() -> None:
    """Maak de demo-wedstrijd en print de links."""
    parser = argparse.ArgumentParser(description="Seed een demo-wedstrijd.")
    parser.add_argument("--demo", action="store_true", help="maak de demo-wedstrijd")
    parser.add_argument(
        "--scores", action="store_true", help="zestien spelers met een ingevulde stand"
    )
    args = parser.parse_args()
    if not args.demo:
        parser.error("gebruik --demo")

    create_all()
    with SessionLocal() as db:
        naam = "Clubkampioenschap 2026" if args.scores else "Demo clubkampioenschap"
        competition = create_competition(db, naam)
        result = import_csv(db, competition, showcase_csv() if args.scores else DEMO_CSV)
        if not result.ok:
            for error in result.errors:
                print(f"FOUT: {error}")
            return

        if args.scores:
            vul_scores(db, competition)

        print(f"\nCompetitie: {competition.name}")
        bord = f"{settings.base_url}/l/{competition.leaderboard_slug}"
        print(f"Leaderboard ronde 1: {bord}?r=1")
        print(f"Leaderboard ronde 2: {bord}?r=2\n")
        for name, round_no, token in result.new_links[:6]:
            print(f"Ronde {round_no}  {name:<16} {settings.base_url}/t/{token}")
        if len(result.new_links) > 6:
            print(f"... en nog {len(result.new_links) - 6} spelerslinks")
        print(f"\nAdmin: {settings.base_url}/admin")


if __name__ == "__main__":
    main()
