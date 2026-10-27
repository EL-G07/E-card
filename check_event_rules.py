from validation import validate_event

GOOD = {"title": "Juma and Anna's anniversary", "type": "Anniversary",
        "date_time": "2030-01-05T09:30", "venue": "Highland hall",
        "description": "Dress code: smart casual"}

def case(label, expected, **changes):
    form = {**GOOD, **changes}
    _, errors = validate_event(form)
    got = set(errors)
    mark = "PASS" if got == set(expected) else "FAIL"
    print(f"{mark}  {label:34} expected {sorted(expected)!s:18} got {sorted(got)}")

case("everything valid", [])
case("title empty", ["title"], title="")
case("title spaces only", ["title"], title="     ")
case("title one word", [], title="Graduation")
case("title 50 characters", [], title="x" * 50)
case("title 51 characters", ["title"], title="x" * 51)
case("type empty", ["type"], type="")
case("type 51 characters", ["type"], type="x" * 51)
case("date empty", ["date_time"], date_time="")
case("date garbage", ["date_time"], date_time="hello")
case("date in the past", ["date_time"], date_time="2001-01-05T09:30")
case("venue empty", ["venue"], venue="")
case("venue 90 characters", [], venue="x" * 90)
case("venue 91 characters", ["venue"], venue="x" * 91)
case("description empty (allowed)", [], description="")
case("description 400 characters", [], description="x" * 400)
case("description 401 characters", ["description"], description="x" * 401)