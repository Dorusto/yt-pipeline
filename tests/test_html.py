from common.html import open_state_script


def test_open_state_script_uses_the_prefix():
    why = open_state_script("why")
    assert "'why:'+d.id" in why
    assert "sessionStorage.getItem" in why
    assert "sessionStorage.setItem" in why
    assert why.startswith("<script>")
    assert why.rstrip().endswith("</script>")


def test_open_state_script_shots_prefix():
    assert "'shots:'+d.id" in open_state_script("shots")
