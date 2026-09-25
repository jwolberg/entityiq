"""Deterministic generator for the adversarial name corpus (IS1-T6, 0034).

Regenerate the checked-in file after editing this generator:

    python -m tests.screening.corpus.generate

All identities are fictional combinations. Each case labels which watchlist
records are the *same person* as the subject (``expected_hits``); everything
else a blocker returns is a false positive. The categories are the collision
types the PRD names (PRD-IDV F6, §[7]).
"""

from __future__ import annotations

import json
from pathlib import Path

OUT = Path(__file__).with_name("v2.json")


def _rec(rid, primary, *, aliases=(), original=None, dobs=(), nat=(), docs=()):
    names = [{"name": primary, "kind": "primary"}]
    if original:
        names.append({"name": original, "kind": "original", "script": "original"})
    names += [{"name": a, "kind": "aka"} for a in aliases]
    return {
        "id": rid,
        "primary_name": primary,
        "names": names,
        "dobs": list(dobs),
        "nationalities": list(nat),
        "documents": list(docs),
    }


def _case(cid, category, name, hits, *, dob=None, nationality=None, documents=()):
    return {
        "id": cid,
        "category": category,
        "subject": {
            "name": name,
            "dob": dob,
            "nationality": nationality,
            "documents": list(documents),
        },
        "expected_hits": list(hits),
    }


def build() -> dict:
    watchlist: list[dict] = []
    cases: list[dict] = []

    def add(category, rows):
        for row in rows:
            rec, subjects = row
            watchlist.append(rec)
            for subject_name, dob, hit in subjects:
                cid = f"C{len(cases) + 1:03d}"
                cases.append(
                    _case(
                        cid, category, subject_name, [rec["id"]] if hit else [], dob=dob
                    )
                )

    counter = [0]

    def wid():
        counter[0] += 1
        return f"W{counter[0]:03d}"

    # 1. Transliteration: the list has the original script plus one romanization;
    #    the subject uses a different romanization.
    translit = [
        ("Dmitri Korolenko", "Дмитрий Короленко", "Dmitriy Korolenko", "1969-02-11"),
        ("Sergei Volynsky", "Сергей Волынский", "Sergey Volynskiy", "1974-07-30"),
        ("Yulia Chernovskaya", "Юлия Черновская", "Iuliia Chernovskaia", "1981-05-19"),
        ("Aleksei Trubetskov", "Алексей Трубецков", "Alexey Trubetskov", "1966-12-03"),
        (
            "Ksenia Miroshnikova",
            "Ксения Мирошникова",
            "Kseniya Miroshnikova",
            "1990-09-09",
        ),
        ("Mohammed Al-Farouqi", "محمد الفاروقي", "Muhammad al Faruqi", "1972-01-25"),
        ("Yousef Haddadin", "يوسف حدادين", "Yusuf Hadadin", "1979-08-14"),
        (
            "Abdulrahman Qassemi",
            "عبد الرحمن قاسمي",
            "Abd al-Rahman Kasimi",
            "1963-04-02",
        ),
        ("Khaled Mansouri", "خالد منصوري", "Khalid Mansuri", "1985-10-21"),
        ("Hussein Tabbakh", "حسين طباخ", "Husayn Tabakh", "1977-03-17"),
    ]
    add(
        "transliteration",
        [
            (
                _rec(wid(), latin, original=orig, dobs=[{"date": dob}]),
                [(variant, dob, True)],
            )
            for latin, orig, variant, dob in translit
        ],
    )

    # 2. Name-order inversion (surname-first listing).
    inversion = [
        ("ZHANG Weiming", "Weiming Zhang"),
        ("LIANG Haoran", "Haoran Liang"),
        ("PARK Jiwoo", "Jiwoo Park"),
        ("TANAKA Hiroshi", "Hiroshi Tanaka"),
        ("NGUYEN Thanh Binh", "Thanh Binh Nguyen"),
        ("KOVACS Bence", "Bence Kovacs"),
        ("CHEN Yuxuan", "Yuxuan Chen"),
        ("OKAFOR Chidi", "Chidi Okafor"),
        ("HORVATH Zsofia", "Zsofia Horvath"),
        ("WATANABE Emi", "Emi Watanabe"),
    ]
    add(
        "inversion",
        [
            (
                _rec(wid(), listed, dobs=[{"year": 1970 + i}]),
                [(subject, f"{1970 + i}-06-01", True)],
            )
            for i, (listed, subject) in enumerate(inversion)
        ],
    )

    # 3. Patronymics / nasab dropped or added.
    patronymic = [
        ("Viktor Andreyevich Morozko", "Viktor Morozko"),
        ("Oksana Petrenkivna Savchuk", "Oksana Savchuk"),
        ("Igor Stanislavovich Belyaknin", "Igor Belyaknin"),
        ("Lyudmila Borisovna Kasatkina", "Lyudmila Kasatkina"),
        ("Saeed bin Rashid Al-Kuwaitri", "Saeed Al-Kuwaitri"),
        ("Omar ibn Khalil Nasrallahi", "Omar Nasrallahi"),
        ("Faisal bin Hamad Al-Dosarri", "Faisal Al-Dosarri"),
        ("Arman Serikovich Zhakenov", "Arman Zhakenov"),
        ("Gulnara Timurovna Iskandrova", "Gulnara Iskandrova"),
        ("Rustam Bakhtiyorovich Umarkulov", "Rustam Umarkulov"),
    ]
    add(
        "patronymic",
        [
            (
                _rec(wid(), listed, dobs=[{"date": f"19{60 + i}-03-0{i % 9 + 1}"}]),
                [(subject, f"19{60 + i}-03-0{i % 9 + 1}", True)],
            )
            for i, (listed, subject) in enumerate(patronymic)
        ],
    )

    # 4. Nicknames / hypocoristics.
    nickname = [
        ("William Harcourtney", "Bill Harcourtney"),
        ("Robert Ellingsworth", "Bob Ellingsworth"),
        ("Aleksandr Pechorsky", "Sasha Pechorsky"),
        ("Katherine Vandermolen", "Kate Vandermolen"),
        ("Richard Blackstonley", "Dick Blackstonley"),
        ("Margaret Oyelowo-Finch", "Peggy Oyelowo-Finch"),
        ("Francisco Ybarrondo", "Paco Ybarrondo"),
        ("Elizabeth Marchwood", "Liz Marchwood"),
        ("Giuseppe Brancatello", "Beppe Brancatello"),
        ("Johannes Vrieslander", "Hans Vrieslander"),
    ]
    add(
        "nickname",
        [
            (
                _rec(wid(), listed, dobs=[{"date": f"19{55 + i}-11-2{i % 9}"}]),
                [(subject, f"19{55 + i}-11-2{i % 9}", True)],
            )
            for i, (listed, subject) in enumerate(nickname)
        ],
    )

    # 5. Initials.
    initials = [
        ("John Patrick Wexfordham", "J. P. Wexfordham"),
        ("Maria Luisa Castellanoz", "M. L. Castellanoz"),
        ("David Aaron Kellerstein", "D. Kellerstein"),
        ("Hannah Rose Pemberwick", "H R Pemberwick"),
        ("Samuel Oduya Ekwensile", "S. O. Ekwensile"),
        ("Lucia Beatriz Montalvez", "L. Montalvez"),
        ("Thomas Edward Ashcombe", "T.E. Ashcombe"),
        ("Priya Lakshmi Venkatram", "P. L. Venkatram"),
        ("Andrzej Marek Wolanski", "A. M. Wolanski"),
        ("Fatima Zahra Benkirane", "F. Z. Benkirane"),
    ]
    add(
        "initials",
        [
            (
                _rec(wid(), listed, dobs=[{"date": f"19{70 + i}-04-1{i % 9}"}]),
                [(subject, f"19{70 + i}-04-1{i % 9}", True)],
            )
            for i, (listed, subject) in enumerate(initials)
        ],
    )

    # 6. Common-name clusters: several listed people share a name; only DOB
    #    tells them apart. Subjects hit exactly one, or none.
    cluster_names = [
        "Ahmed Khan",
        "Maria Garcia",
        "Li Wei",
        "Mohammed Ali",
        "John Smith",
    ]
    for j, name in enumerate(cluster_names):
        ids = []
        for k, year in enumerate((1965, 1978, 1991)):
            rec = _rec(wid(), name, dobs=[{"date": f"{year}-0{j + 1}-1{k}"}])
            watchlist.append(rec)
            ids.append(rec["id"])
        cid = f"C{len(cases) + 1:03d}"
        cases.append(
            _case(cid, "common_name_cluster", name, [ids[1]], dob=f"1978-0{j + 1}-11")
        )
        cid = f"C{len(cases) + 1:03d}"
        cases.append(
            _case(cid, "common_name_cluster", name, [], dob=f"2001-0{j + 1}-15")
        )

    # 7. Partial / approximate DOBs on the list.
    partial = [
        ({"year": 1962}, "1962-08-30", True),
        ({"year": 1962}, "1963-08-30", False),
        ({"year": 1975, "circa": True}, "1976-02-02", True),
        ({"year": 1975, "circa": True}, "1985-02-02", False),
        ({"from_year": 1980, "to_year": 1984}, "1983-12-31", True),
        ({"from_year": 1980, "to_year": 1984}, "1986-01-01", False),
        ({"year": 1959, "month": 7}, "1959-07-04", True),
        ({"year": 1959, "month": 7}, "1959-08-04", False),
        ({"year": 1971}, "1971-01-01", True),
        ({"from_year": 1990, "to_year": 1991}, "1990-06-15", True),
    ]
    partial_names = [
        "Teodor Vasilescu",
        "Ingrid Solheimsen",
        "Rafael Quintanero",
        "Nadia Bouhaddou",
        "Emeka Nwachukwuma",
        "Soraya Hashemizadeh",
        "Pavel Dvorakek",
        "Helena Lindqvistad",
        "Arturo Benavidesco",
        "Zeynep Karaosmanli",
    ]
    for name, (dob, subject_dob, hit) in zip(partial_names, partial, strict=True):
        rec = _rec(wid(), name, dobs=[dob])
        watchlist.append(rec)
        cid = f"C{len(cases) + 1:03d}"
        cases.append(
            _case(cid, "partial_dob", name, [rec["id"]] if hit else [], dob=subject_dob)
        )

    # 8. Corroborated matches: name + full DOB + passport all agree (MATCH).
    corroborated = [
        "Oleksandr Vyshnevetsky",
        "Mariam Tadevosyan",
        "Kwabena Asantewaa",
        "Svetlana Rudakovskaya",
        "Tariq Bensalimi",
        "Jovana Petrovicic",
        "Rashid Karimzadeh",
        "Elif Canbolatli",
        "Dmytro Hordiyenkiv",
        "Nasrin Farahmandi",
    ]
    for i, name in enumerate(corroborated):
        dob = f"19{60 + i}-0{i % 9 + 1}-1{i % 9}"
        passport = {"type": "passport", "number": f"PX{900100 + i}", "country": "UT"}
        rec = _rec(wid(), name, dobs=[{"date": dob}], docs=[passport])
        watchlist.append(rec)
        cid = f"C{len(cases) + 1:03d}"
        cases.append(
            _case(
                cid,
                "corroborated_match",
                name,
                [rec["id"]],
                dob=dob,
                documents=[passport],
            )
        )

    # 9. Clean subjects: nobody on the list resembles them (auto-CLEAR).
    clean = [
        "Aurelio Fenwicke",
        "Brunhilde Okonkwo-Lang",
        "Cassius Thornquist",
        "Delphine Mbatha-Ruiz",
        "Evander Kowalczykow",
        "Freya Nakashima-Holt",
        "Gideon Ashworth-Obi",
        "Hortensia Valderrama",
        "Ignatius Pellegrinet",
        "Juniper Vasquez-Lund",
    ]
    for name in clean:
        cid = f"C{len(cases) + 1:03d}"
        cases.append(_case(cid, "clean", name, [], dob="1990-01-01"))

    return {"version": 2, "watchlist": watchlist, "cases": cases}


if __name__ == "__main__":
    OUT.write_text(json.dumps(build(), ensure_ascii=False, indent=1) + "\n")
    print(f"wrote {OUT}")
