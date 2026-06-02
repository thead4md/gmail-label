"""Altair chart builders for the INSIGHTS tab. Dark-theme styled."""

from __future__ import annotations

import altair as alt
import pandas as pd

from mailmind.dashboard.theme import label_color, channel_color

_AXIS = dict(labelColor="#94A3B8", titleColor="#94A3B8")


def label_distribution_chart(rows: list):
    if not rows:
        return None
    df = pd.DataFrame(rows)
    df["color"] = df["label"].apply(lambda l: label_color(str(l).upper()))
    return (alt.Chart(df).mark_arc(innerRadius=50)
            .encode(theta="count:Q",
                    color=alt.Color("color:N", scale=None, legend=None),
                    tooltip=["label", "count"])
            .properties(height=220, background="transparent"))


def channel_distribution_chart(rows: list):
    if not rows:
        return None
    df = pd.DataFrame(rows)
    df["color"] = df["channel"].apply(channel_color)
    return (alt.Chart(df).mark_bar(cornerRadius=4)
            .encode(x=alt.X("channel:N", sort="-y", axis=alt.Axis(**_AXIS, title=None)),
                    y=alt.Y("count:Q", axis=alt.Axis(**_AXIS, title=None, gridColor="#1C2237")),
                    color=alt.Color("color:N", scale=None, legend=None),
                    tooltip=["channel", "count"])
            .properties(height=200, background="transparent").configure_view(strokeWidth=0))


def top_senders_chart(rows: list):
    if not rows:
        return None
    df = pd.DataFrame(rows)
    return (alt.Chart(df).mark_bar(cornerRadius=4)
            .encode(y=alt.Y("sender:N", sort="-x", axis=alt.Axis(**_AXIS, title=None)),
                    x=alt.X("volume:Q", axis=alt.Axis(**_AXIS, title=None, gridColor="#1C2237")),
                    color=alt.Color("approval_rate:Q",
                                    scale=alt.Scale(scheme="blues"), legend=None),
                    tooltip=["sender", "volume", "approval_rate"])
            .properties(height=260, background="transparent").configure_view(strokeWidth=0))


def decision_time_chart(rows: list):
    if not rows:
        return None
    df = pd.DataFrame(rows)
    return (alt.Chart(df).mark_bar(color="#5B8AF0")
            .encode(x=alt.X("minutes:Q", bin=alt.Bin(maxbins=20),
                            axis=alt.Axis(**_AXIS, title="minutes to decision")),
                    y=alt.Y("count():Q", axis=alt.Axis(**_AXIS, title=None, gridColor="#1C2237")))
            .properties(height=200, background="transparent").configure_view(strokeWidth=0))


def channel_weekday_heatmap(rows: list):
    if not rows:
        return None
    names = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"]
    df = pd.DataFrame(rows)
    df["day"] = df["weekday"].apply(lambda w: names[int(w)] if 0 <= int(w) <= 6 else "?")
    return (alt.Chart(df).mark_rect()
            .encode(x=alt.X("day:N", sort=names, axis=alt.Axis(**_AXIS, title=None)),
                    y=alt.Y("channel:N", axis=alt.Axis(**_AXIS, title=None)),
                    color=alt.Color("count:Q", scale=alt.Scale(scheme="blues"), legend=None),
                    tooltip=["channel", "day", "count"])
            .properties(height=220, background="transparent").configure_view(strokeWidth=0))
