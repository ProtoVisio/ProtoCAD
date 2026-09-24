from protocad.prep.names import solver_name, solver_names


def test_transliteration_and_safety():
    assert solver_name("Заделка") == "Zadelka"
    assert solver_name("стык А | Б") == "styk_A_B"
    assert solver_name("1-я опора") == "G_1_ya_opora"
    assert len(solver_name("о" * 100)) == 32


def test_names_do_not_collide_ignoring_case():
    names = solver_names(["Вал", "ВАЛ", "val"])
    assert len({value.upper() for value in names.values()}) == 3
