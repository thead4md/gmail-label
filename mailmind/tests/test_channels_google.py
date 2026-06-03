from mailmind.intelligence.channels import detect_channel


def test_docs_comment():
    assert detect_channel("Adam commented on Q3 plan",
        "comments-noreply@docs.google.com", "view the comment") == "docs"


def test_calendar_invite():
    assert detect_channel("Invitation: Standup",
        "calendar-notification@google.com", "you are invited") == "calendar"


def test_calendar_hungarian():
    assert detect_channel("Naptár: Megbeszélés", "x@y.hu", "esemény meghívó") == "calendar"


def test_tasks_complete():
    assert detect_channel("Task completed",
        "tasks-noreply@google.com", "marked complete") == "tasks"


def test_docs_not_newsletter():
    assert detect_channel("shared a document with you",
        "drive-shares-noreply@google.com", "leiratkozás") != "newsletter"
