"""Dashboard pages; each one answers a question a SkyRoute risk analyst asks."""

from dataclasses import dataclass, replace

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from plotly.subplots import make_subplots

from fraud_intel.config import AppConfig
from fraud_intel.dashboard.filters import Filters, apply_filters
from fraud_intel.domain.metrics import comparison_sentence, headline, segment_scorecard, segment_summary
from fraud_intel.domain.metrics import score_check as measure_score
from fraud_intel.domain.report import daily_top, summary_insight


@dataclass(frozen=True)
class PageContext:
    scored: pd.DataFrame
    alerts: pd.DataFrame
    filters: Filters
    config: AppConfig


def _palette() -> dict[str, str]:
    # Yuno's brand colors, taken from y.uno: indigo accent, its tints, near-black text, lime highlight
    return {
        "indigo": "#3E4FE0",
        "indigo_dark": "#1E2258",
        "indigo_light": "#6B7BFF",
        "indigo_soft": "#939FFF",
        "indigo_subtle": "#DDE6FF",
        "lime": "#C7E956",
        "ink": "#0A0A0A",
        "grey": "#737373",
        "grid": "#E5E5E5",
        "alert": "#EF4444",
    }


def _md(text: str) -> str:
    # Streamlit renders $...$ as LaTeX, so two amounts in one line would turn into a formula
    return text.replace("$", "\\$")


def _all_levels(filters: Filters) -> Filters:
    # trend charts and history comparisons need every row, including unscored history, so drop the risk filter
    return replace(filters, risk_levels=())


def _layout(figure: go.Figure, title: str, height: int) -> go.Figure:
    figure.update_layout(
        title={"text": title, "font": {"size": 15}},
        height=height,
        margin={"l": 10, "r": 10, "t": 48, "b": 10},
        template="plotly_white",
        font={"family": "Geist, Inter, sans-serif", "color": _palette()["ink"]},
        colorway=[_palette()["indigo"], _palette()["indigo_soft"], _palette()["lime"]],
        legend={"orientation": "h", "yanchor": "bottom", "y": 1.0, "xanchor": "right", "x": 1},
    )
    return figure


def overview(context: PageContext) -> None:
    filters, fee = context.filters, context.config.chargeback_fee_usd
    current = apply_filters(frame=context.scored, filters=filters, use_dates=True)
    history = apply_filters(
        frame=context.scored[~context.scored["in_scored_window"]], filters=_all_levels(filters), use_dates=False
    )
    st.caption(
        f"{filters.start:%d %b} to {filters.end:%d %b %Y} · {len(current):,} payment attempts · "
        "the filters above apply to every page"
    )
    if current.empty:
        st.warning("No transactions match the filters.")
        return

    now, before = headline(frame=current, chargeback_fee_usd=fee), headline(frame=history, chargeback_fee_usd=fee)
    high = current[current["risk_level"] == "high"]
    tiles = st.columns(4)
    tiles[0].metric(
        "Fraud rate",
        f"{now['fraud_rate']:.2%}",
        # a batch without earlier months has nothing to compare against
        delta=None
        if history.empty
        else f"{(now['fraud_rate'] - before['fraud_rate']) * 100:+.2f} pp vs history ({before['fraud_rate']:.2%})",
        delta_color="inverse",
        border=True,
    )
    tiles[1].metric(
        "Chargebacks",
        f"{now['frauds']:,}",
        delta=_md(f"${now['chargeback_cost_usd']:,.0f} lost incl. fees"),
        delta_color="off",
        delta_arrow="off",
        border=True,
    )
    tiles[2].metric(
        "Authorization rate",
        f"{now['auth_rate']:.1%}",
        delta="healthy band: 82-85%",
        delta_color="off",
        delta_arrow="off",
        border=True,
    )
    tiles[3].metric(
        "High-risk bookings",
        f"{len(high):,}",
        delta=_md(f"${high['amount_usd'].sum():,.0f} at stake"),
        delta_color="off",
        delta_arrow="off",
        border=True,
    )

    segments = segment_summary(frame=current, by=["billing_country", "payment_method"], chargeback_fee_usd=fee)
    sentence = comparison_sentence(
        segments=segments, label_columns=["billing_country", "payment_method"], min_approved=30
    )
    worst = segments.sort_values("chargeback_cost_usd", ascending=False).iloc[0]
    st.info(
        _md(
            (f"**{sentence}** " if sentence else "")
            + f"The costliest segment is {worst['billing_country']} {worst['payment_method']}: "
            f"{int(worst['frauds'])} chargebacks, ${worst['chargeback_cost_usd']:,.0f} lost."
        )
    )

    trend_source = apply_filters(frame=context.scored, filters=_all_levels(filters), use_dates=False)
    daily = segment_summary(frame=trend_source, by=["date"], chargeback_fee_usd=fee).sort_values("date")
    scored_start = context.scored.loc[context.scored["in_scored_window"], "timestamp_utc"].min().normalize()
    left, right = st.columns([3, 2])
    with left:
        st.plotly_chart(_daily_fraud_chart(daily=daily, scored_start=scored_start), width="stretch")
    with right:
        st.plotly_chart(_heatmap(segments=segments), width="stretch")

    st.subheader("Where is the money going?")
    top_segments = segment_summary(
        frame=current, by=["billing_country", "payment_method", "customer_type"], chargeback_fee_usd=fee
    ).sort_values("chargeback_cost_usd", ascending=False)
    st.dataframe(
        _percent(top_segments.head(10), columns=["fraud_rate", "auth_rate"]),
        hide_index=True,
        width="stretch",
        column_config={
            "billing_country": "Country",
            "payment_method": "Method",
            "customer_type": "Customer",
            "attempts": st.column_config.NumberColumn("Attempts", format="localized"),
            "approved": st.column_config.NumberColumn("Approved", format="localized"),
            "frauds": "Chargebacks",
            "fraud_usd": None,
            "avg_amount_usd": st.column_config.NumberColumn("Avg booking", format="$%.0f"),
            "auth_rate": st.column_config.NumberColumn("Auth rate", format="%.1f%%"),
            "fraud_rate": st.column_config.NumberColumn("Fraud rate", format="%.2f%%"),
            "chargeback_cost_usd": st.column_config.NumberColumn("Lost (incl. fees)", format="$%.0f"),
        },
    )
    st.plotly_chart(_auth_rate_chart(daily=daily, scored_start=scored_start), width="stretch")


def _daily_fraud_chart(daily: pd.DataFrame, scored_start: pd.Timestamp) -> go.Figure:
    history = daily[daily["date"] < scored_start]
    # a spike is a day more than two standard deviations above the settled history
    threshold = history["fraud_rate"].mean() + 2 * history["fraud_rate"].std() if len(history) > 2 else None
    figure = go.Figure()
    figure.add_bar(
        x=daily["date"], y=daily["fraud_rate"], name="Daily fraud rate", marker_color=_palette()["indigo_subtle"]
    )
    figure.add_scatter(
        x=daily["date"],
        y=daily["fraud_rate"].rolling(7, min_periods=1).mean(),
        name="7-day average",
        line={"color": _palette()["indigo"], "width": 3},
    )
    if threshold is not None:
        spikes = daily[daily["fraud_rate"] > threshold]
        figure.add_scatter(
            x=spikes["date"],
            y=spikes["fraud_rate"],
            mode="markers",
            name="Spike day",
            marker={"color": _palette()["alert"], "size": 9, "line": {"color": "white", "width": 1}},
        )
    # mark where scoring starts only when there is history before it to compare with
    if not history.empty:
        figure.add_vline(x=scored_start, line_dash="dash", line_color="#555")
        figure.add_annotation(
            x=scored_start,
            y=1,
            yref="paper",
            text="scored window starts",
            showarrow=False,
            xanchor="left",
            yanchor="top",
        )
    figure.update_yaxes(tickformat=".1%", title=None)
    return _layout(figure=figure, title="Fraud rate by day (chargebacks / approved)", height=380)


def _auth_rate_chart(daily: pd.DataFrame, scored_start: pd.Timestamp) -> go.Figure:
    figure = go.Figure()
    figure.add_hrect(y0=0.82, y1=0.85, fillcolor=_palette()["lime"], opacity=0.35, line_width=0)
    figure.add_scatter(
        x=daily["date"], y=daily["auth_rate"], name="Authorization rate", line={"color": _palette()["indigo"]}
    )
    if (daily["date"] < scored_start).any():
        figure.add_vline(x=scored_start, line_dash="dash", line_color="#555")
    figure.update_yaxes(tickformat=".0%", range=[0.7, 0.95], title=None)
    return _layout(figure=figure, title="Authorization rate by day (lime band: healthy 82-85%)", height=280)


def _heatmap(segments: pd.DataFrame) -> go.Figure:
    rates = segments.pivot(index="billing_country", columns="payment_method", values="fraud_rate")
    counts = segments.pivot(index="billing_country", columns="payment_method", values="approved")
    text = [
        ["" if pd.isna(rate) else f"{rate:.1%}" for rate, count in zip(rate_row, count_row, strict=True)]
        for rate_row, count_row in zip(rates.to_numpy(), counts.to_numpy(), strict=True)
    ]
    figure = go.Figure(
        go.Heatmap(
            z=rates.to_numpy(),
            x=list(rates.columns),
            y=list(rates.index),
            text=text,
            texttemplate="%{text}",
            colorscale=[
                [0, "#F6F7FB"],
                [0.25, _palette()["indigo_subtle"]],
                [0.6, _palette()["indigo_light"]],
                [1, _palette()["indigo_dark"]],
            ],
            colorbar={"tickformat": ".1%", "title": None},
            customdata=counts.to_numpy(),
            hovertemplate="%{y} %{x}: %{z:.2%} of %{customdata:,} approved<extra></extra>",
            textfont={"size": 13},
        )
    )
    figure.update_yaxes(autorange="reversed")
    return _layout(figure=figure, title="Which country and method is riskiest?", height=380)


def _percent(frame: pd.DataFrame, columns: list[str]) -> pd.DataFrame:
    shown = frame.copy()
    for column in columns:
        shown[column] = shown[column] * 100
    return shown


def countries(context: PageContext) -> None:
    _segment_page(context=context, dimension="billing_country", other="payment_method", noun="country")


def payment_methods(context: PageContext) -> None:
    _segment_page(context=context, dimension="payment_method", other="billing_country", noun="payment method")


def _segment_page(context: PageContext, dimension: str, other: str, noun: str) -> None:
    filters, fee = context.filters, context.config.chargeback_fee_usd
    current = apply_filters(frame=context.scored, filters=filters, use_dates=True)
    history = apply_filters(
        frame=context.scored[~context.scored["in_scored_window"]], filters=_all_levels(filters), use_dates=False
    )
    st.caption(
        f"{filters.start:%d %b} to {filters.end:%d %b %Y} · each {noun} compared with the settled 30 days before "
        "· ordered by money lost"
    )
    if current.empty:
        st.warning("No transactions match the filters.")
        return
    card = segment_scorecard(current=current, history=history, by=dimension, chargeback_fee_usd=fee)
    _segment_cards(card=card, dimension=dimension)

    order = list(card[dimension])
    trend_source = apply_filters(frame=context.scored, filters=_all_levels(filters), use_dates=False)
    scored_start = context.scored.loc[context.scored["in_scored_window"], "timestamp_utc"].min().normalize()
    st.plotly_chart(
        _segment_trends(
            frame=trend_source, dimension=dimension, order=order, scored_start=scored_start, chargeback_fee_usd=fee
        ),
        width="stretch",
    )
    st.plotly_chart(
        _segment_split(frame=current, dimension=dimension, other=other, order=order, chargeback_fee_usd=fee),
        width="stretch",
    )
    st.dataframe(
        _percent(card, columns=["fraud_rate", "history_fraud_rate", "auth_rate"]),
        hide_index=True,
        width="stretch",
        column_order=[
            dimension,
            "attempts",
            "approved",
            "auth_rate",
            "frauds",
            "fraud_rate",
            "history_fraud_rate",
            "change_pp",
            "chargeback_cost_usd",
            "avg_amount_usd",
            "high_risk",
        ],
        column_config={
            # short headers keep all eleven columns inside the page width
            dimension: "Country" if dimension == "billing_country" else "Method",
            "attempts": st.column_config.NumberColumn("Attempts", format="localized"),
            "approved": st.column_config.NumberColumn("Approved", format="localized"),
            "auth_rate": st.column_config.NumberColumn("Auth rate", format="%.1f%%"),
            "frauds": "Chargebacks",
            "fraud_rate": st.column_config.NumberColumn("Fraud rate", format="%.2f%%"),
            "history_fraud_rate": st.column_config.NumberColumn("Last month", format="%.2f%%"),
            "change_pp": st.column_config.NumberColumn("Change", format="%+.2f pp"),
            "chargeback_cost_usd": st.column_config.NumberColumn(
                "Lost", format="$%.0f", help="Chargeback value plus the fee per chargeback"
            ),
            "avg_amount_usd": st.column_config.NumberColumn("Avg booking", format="$%.0f"),
            "high_risk": "High risk",
        },
    )


def _segment_cards(card: pd.DataFrame, dimension: str) -> None:
    per_row = 5
    for start in range(0, len(card), per_row):
        chunk = card.iloc[start : start + per_row]
        for column, (_, row) in zip(st.columns(per_row), chunk.iterrows(), strict=False):
            with column.container(border=True):
                change, before = row["change_pp"], row["history_fraud_rate"]
                st.metric(
                    str(row[dimension]),
                    f"{row['fraud_rate']:.2%}",
                    delta=None if pd.isna(change) else f"{change:+.2f} pp",
                    delta_color="inverse",
                    help="Fraud rate = chargebacks / approved bookings; the change is against last month",
                )
                # short lines, so every card wraps the same way in a narrow column
                last_month = "n/a" if pd.isna(before) else f"{before:.2%}"
                st.caption(
                    _md(
                        f"**${row['chargeback_cost_usd']:,.0f}** lost  \n"
                        f"{int(row['frauds']):,} chargebacks  \n"
                        f"**{int(row['high_risk']):,}** high risk  \n"
                        f"Auth rate {row['auth_rate']:.1%}  \n"
                        f"Last month {last_month}"
                    )
                )


def _segment_trends(
    frame: pd.DataFrame, dimension: str, order: list[str], scored_start: pd.Timestamp, chargeback_fee_usd: float
) -> go.Figure:
    daily = segment_summary(frame=frame, by=["date", dimension], chargeback_fee_usd=chargeback_fee_usd)
    has_history = bool((daily["date"] < scored_start).any())
    figure = make_subplots(rows=1, cols=len(order), shared_yaxes=True, subplot_titles=order, horizontal_spacing=0.02)
    for position, segment in enumerate(order, start=1):
        rows = daily[daily[dimension] == segment].sort_values("date")
        # a 7-day rate from summed counts, so quiet days in small segments do not swing the line
        weekly = rows["frauds"].rolling(7, min_periods=1).sum() / rows["approved"].rolling(7, min_periods=1).sum()
        figure.add_bar(
            x=rows["date"],
            y=rows["fraud_rate"],
            marker_color=_palette()["indigo_subtle"],
            showlegend=False,
            row=1,
            col=position,
        )
        figure.add_scatter(
            x=rows["date"],
            y=weekly,
            line={"color": _palette()["indigo"], "width": 2.5},
            showlegend=False,
            row=1,
            col=position,
        )
        if has_history:
            figure.add_vline(x=scored_start, line_dash="dot", line_color=_palette()["grey"], row=1, col=position)
    figure.update_yaxes(tickformat=".0%")
    # three ticks per small panel; more would overlap at this width
    figure.update_xaxes(tickformat="%d %b", dtick=21 * 24 * 3600 * 1000, tick0=frame["date"].min())
    return _layout(
        figure=figure,
        title="Daily fraud rate, same scale for every panel (line: 7-day rate; dotted: scored window)",
        height=300,
    )


def _segment_split(
    frame: pd.DataFrame, dimension: str, other: str, order: list[str], chargeback_fee_usd: float
) -> go.Figure:
    split = segment_summary(frame=frame, by=[dimension, other], chargeback_fee_usd=chargeback_fee_usd)
    colors = [_palette()[name] for name in ("indigo", "indigo_soft", "lime", "indigo_dark", "grey")]
    others = split.groupby(other)["chargeback_cost_usd"].sum().sort_values(ascending=False).index
    figure = go.Figure()
    for index, value in enumerate(others):
        part = split[split[other] == value].set_index(dimension).reindex(order[::-1])
        figure.add_bar(
            y=order[::-1],
            x=part["chargeback_cost_usd"].fillna(0),
            name=str(value),
            orientation="h",
            marker_color=colors[index % len(colors)],
            hovertemplate=f"%{{y}} {value}: $%{{x:,.0f}} lost<extra></extra>",
        )
    # list legend entries in stacking order, biggest share first
    figure.update_layout(barmode="stack", legend={"traceorder": "normal"})
    figure.update_xaxes(tickprefix="$", tickformat=",.0f")
    label = "payment method" if other == "payment_method" else "country"
    return _layout(figure=figure, title=f"Money lost to chargebacks, split by {label}", height=320)


def patterns(context: PageContext) -> None:
    fee = context.config.chargeback_fee_usd
    current = apply_filters(frame=context.scored, filters=context.filters, use_dates=True)
    if current.empty:
        st.warning("No transactions match the filters.")
        return
    dimensions = {
        "Hour of day (customer's local time)": "booking_hour",
        "Booking value": "value_band",
        "New vs returning customer": "customer_type",
        "IP country vs billing country": "ip_matches_billing",
        "Days to departure": "days_to_departure",
        "Booking type": "booking_type",
        "Payment method": "payment_method",
        "Country": "billing_country",
    }
    label = st.selectbox("Slice fraud by", list(dimensions), key="pattern_dimension")
    column = dimensions[label]
    segments = segment_summary(frame=current, by=[column], chargeback_fee_usd=fee).sort_values(column)
    base_rate = headline(frame=current, chargeback_fee_usd=fee)["fraud_rate"]
    sentence = comparison_sentence(segments=segments, label_columns=[column], min_approved=50)
    if sentence:
        st.info(f"**{sentence}** Average across the filtered bookings: {base_rate:.2%}.")

    left, right = st.columns(2)
    names = segments[column].astype(str)
    rate_chart = go.Figure(go.Bar(x=names, y=segments["fraud_rate"], marker_color=_palette()["indigo"]))
    rate_chart.add_hline(y=base_rate, line_dash="dash", annotation_text="average", line_color="#555")
    rate_chart.update_yaxes(tickformat=".1%")
    # 24 hour labels do not fit flat, and vertical text is hard to read
    rate_chart.update_xaxes(tickangle=-45)
    left.plotly_chart(_layout(figure=rate_chart, title=f"Fraud rate by {label.lower()}", height=360), width="stretch")
    volume_chart = go.Figure(go.Bar(x=names, y=segments["attempts"], marker_color=_palette()["indigo_soft"]))
    volume_chart.update_xaxes(tickangle=-45)
    right.plotly_chart(
        _layout(figure=volume_chart, title=f"Payment attempts by {label.lower()}", height=360), width="stretch"
    )

    st.subheader("How often is a booking fraud when a rule fires?")
    approved = current[current["in_scored_window"] & (current["status"] == "approved")]
    fired = approved.assign(rule=approved["rules_fired"].str.split(", ")).explode("rule")
    fired = fired[fired["rule"].fillna("") != ""]
    if fired.empty:
        st.caption("No rules fired for the filtered bookings.")
        return
    by_rule = (
        fired.groupby("rule")
        .agg(bookings=("transaction_id", "count"), fraud_rate=("is_fraud", "mean"))
        .sort_values("fraud_rate")
        .reset_index()
    )
    rule_chart = go.Figure(
        go.Bar(
            y=by_rule["rule"],
            x=by_rule["fraud_rate"],
            orientation="h",
            marker_color=_palette()["indigo_light"],
            text=[
                f"{rate:.1%} of {count:,}"
                for rate, count in zip(by_rule["fraud_rate"], by_rule["bookings"], strict=True)
            ],
            textposition="outside",
        )
    )
    rule_chart.add_vline(
        x=base_rate, line_dash="dash", line_color="#555", annotation_text="average", annotation_position="bottom right"
    )
    rule_chart.update_xaxes(tickformat=".0%", range=[0, by_rule["fraud_rate"].max() * 1.3])
    st.plotly_chart(
        _layout(figure=rule_chart, title="Fraud rate of approved bookings where each rule fired", height=380),
        width="stretch",
    )


def transactions(context: PageContext) -> None:
    current = apply_filters(frame=context.scored, filters=context.filters, use_dates=True)
    ranked = current[current["in_scored_window"]].sort_values(["risk_score", "amount_usd"], ascending=False)
    st.caption(
        f"{len(ranked):,} scored transactions match the filters, ranked by risk score then amount. "
        "Click a row to see why it was flagged."
    )
    shown = ranked.head(500)
    table = shown.loc[
        :,
        [
            "risk_score",
            "risk_level",
            "timestamp_utc",
            "customer_id",
            "billing_country",
            "ip_country",
            "payment_method",
            "amount_usd",
            "status",
            "reasons",
            "recommended_action",
            "transaction_id",
        ],
    ]
    selection = st.dataframe(
        table,
        hide_index=True,
        width="stretch",
        height=420,
        on_select="rerun",
        selection_mode="single-row",
        key="transaction_table",
        column_config={
            "risk_score": st.column_config.ProgressColumn("Score", min_value=0, max_value=100, format="%d"),
            "risk_level": "Level",
            "timestamp_utc": st.column_config.DatetimeColumn("Time (UTC)", format="D MMM HH:mm"),
            "customer_id": "Customer",
            "billing_country": "Country",
            "ip_country": "IP",
            "payment_method": "Method",
            "amount_usd": st.column_config.NumberColumn("Amount", format="$%.0f"),
            "status": "Status",
            "reasons": st.column_config.TextColumn("Why flagged", width="large"),
            "recommended_action": "Action",
            "transaction_id": "ID",
        },
    )
    st.download_button(
        "Download this list (CSV)",
        data=ranked.to_csv(index=False),
        file_name="high_risk_transactions.csv",
        mime="text/csv",
    )
    selected_rows = selection.selection.rows if selection else []
    chosen = shown.iloc[selected_rows[0]] if selected_rows else (shown.iloc[0] if not shown.empty else None)
    if chosen is not None:
        _transaction_detail(row=chosen)


def _transaction_detail(row: pd.Series) -> None:
    st.subheader(f"{row['transaction_id']} · score {row['risk_score']} ({row['risk_level']})")
    facts = st.columns(5)
    facts[0].metric("Amount", f"${row['amount_usd']:,.0f}")
    facts[1].metric("Billing / IP", f"{row['billing_country']} / {row['ip_country']}")
    facts[2].metric(
        "Method",
        row["payment_method"],
        delta=None if pd.isna(row["card_bin"]) else f"BIN {row['card_bin']}",
        delta_color="off",
        delta_arrow="off",
    )
    facts[3].metric("Customer", row["customer_type"])
    facts[4].metric("Departs", "n/a" if pd.isna(row["departure_date"]) else f"{row['departure_date']:%d %b}")
    reasons = [reason for reason in str(row["reasons"]).split("; ") if reason]
    reason_list = "\n".join(f"- {reason}" for reason in reasons)
    st.markdown(_md(f"**Why it was flagged**\n{reason_list}") if reasons else "No rules fired.")
    message = _md(f"Recommended action: **{row['recommended_action']}**")
    if row["risk_level"] == "high":
        st.error(message)
    elif row["risk_level"] == "medium":
        st.warning(message)
    else:
        st.info(message)


def daily_report(context: PageContext) -> None:
    window = context.scored[context.scored["in_scored_window"]]
    first_day, last_day = window["timestamp_utc"].min().date(), window["timestamp_utc"].max().date()
    # country, method, and risk filters apply; the report covers one day, which defaults to the end of the range
    scored = apply_filters(frame=window, filters=context.filters, use_dates=False)
    default_day = min(max(context.filters.end, first_day), last_day)
    day = st.date_input("Day", value=default_day, min_value=first_day, max_value=last_day, key="report_day")
    top = daily_top(frame=scored, day=day, top_n=50)
    st.info(_md(summary_insight(top=top)))
    actions = top["recommended_action"].value_counts()
    tiles = st.columns(4)
    short_labels = {
        "Refund and block card": "Refund and block",
        "Contact customer to verify before travel": "Verify with customer",
        "Manual review": "Manual review",
        "Watch customer and IP": "Watch customer/IP",
    }
    for tile, (action, short) in zip(tiles, short_labels.items(), strict=True):
        tile.metric(short, int(actions.get(action, 0)), border=True, help=action)
    leading = ["rank", "risk_score", "risk_level"]
    st.dataframe(
        top.loc[:, leading + [column for column in top.columns if column not in leading]],
        hide_index=True,
        width="stretch",
        column_config={
            "rank": "#",
            "risk_score": st.column_config.ProgressColumn("Score", min_value=0, max_value=100, format="%d"),
            "risk_level": "Level",
            "transaction_id": "ID",
            "customer_id": "Customer",
            "billing_country": "Country",
            "ip_country": "IP",
            "payment_method": "Method",
            "card_bin": "BIN",
            "status": "Status",
            "recommended_action": "Action",
            "amount_usd": st.column_config.NumberColumn("Amount", format="$%.0f"),
            "timestamp_utc": st.column_config.DatetimeColumn("Time (UTC)", format="HH:mm"),
            "reasons": st.column_config.TextColumn("Why flagged", width="large"),
        },
    )
    left, right = st.columns(2)
    left.download_button("Download CSV", data=top.to_csv(index=False), file_name=f"top50_{day}.csv", mime="text/csv")
    right.download_button(
        "Download JSON",
        data=top.to_json(orient="records", date_format="iso", indent=2) or "[]",
        file_name=f"top50_{day}.json",
        mime="application/json",
    )


def alerts(context: PageContext) -> None:
    feed = context.alerts.copy()
    if feed.empty:
        st.success("No alerts fired.")
        return
    day = feed["timestamp_utc"].dt.date
    feed = feed[(day >= context.filters.start) & (day <= context.filters.end)].sort_values(
        "timestamp_utc", ascending=False
    )
    severity = feed["severity"].value_counts()
    tiles = st.columns(3)
    tiles[0].metric("Alerts in range", len(feed), border=True)
    tiles[1].metric("Critical", int(severity.get("critical", 0)), border=True)
    tiles[2].metric("High", int(severity.get("high", 0)), border=True)
    with st.expander("What triggers an alert?"):
        st.markdown(
            "- **velocity_burst** (critical): one customer makes 5+ attempts in 60 minutes.\n"
            "- **card_testing** (high): 3+ declines from one customer in 30 minutes.\n"
            "- **high_risk_share_spike** (high): the share of high-risk bookings in the last hour is at least "
            "2x its 7-day level. Chargebacks arrive weeks late, so the live signal is the risk score.\n"
            "- **country_high_value_burst** (critical): $800+ card bookings from one country in 24 h reach 3x "
            "that country's daily average over the week before.\n\n"
            "Each alert also goes to a notifier. Today it prints to the console; Slack or PagerDuty would plug "
            "into the same interface."
        )
    feed["severity"] = feed["severity"].map({"critical": "🔴 critical", "high": "🟠 high"}).fillna(feed["severity"])
    st.dataframe(
        feed,
        hide_index=True,
        width="stretch",
        column_config={
            "timestamp_utc": st.column_config.DatetimeColumn("Time (UTC)", format="D MMM HH:mm"),
            "rule": "Rule",
            "severity": "Severity",
            "subject": "Subject",
            "message": st.column_config.TextColumn("What happened", width="large"),
        },
    )


def score_check(context: PageContext) -> None:
    current = apply_filters(frame=context.scored, filters=_all_levels(context.filters), use_dates=True)
    by_level, stats = measure_score(frame=current)
    st.caption(
        "The score never sees fraud labels. Here we compare it with the chargebacks that came in later, "
        "for approved bookings in the scored window."
    )
    tiles = st.columns(4)
    tiles[0].metric("Fraud rate, all approved", f"{stats['base_rate']:.2%}", border=True)
    tiles[1].metric("Fraud rate when 'high'", f"{stats['precision_high']:.0%}", border=True)
    tiles[2].metric("Fraud caught by 'high'", f"{stats['recall_high']:.0%}", border=True)
    tiles[3].metric("Fraud caught by 'medium' or 'high'", f"{stats['recall_medium_or_high']:.0%}", border=True)
    approved = current[current["in_scored_window"] & (current["status"] == "approved")]
    bands = pd.cut(approved["risk_score"].astype(float), bins=list(range(0, 101, 10)), right=False, include_lowest=True)
    by_band = approved.groupby(bands, observed=True)["is_fraud"].agg(["mean", "count"]).reset_index()
    figure = go.Figure(
        go.Bar(
            x=[f"{interval.left:.0f}-{interval.right - 1:.0f}" for interval in by_band["risk_score"]],
            y=by_band["mean"],
            marker_color=_palette()["indigo"],
            text=[f"n={count:,}" for count in by_band["count"]],
            textposition="outside",
        )
    )
    figure.update_yaxes(tickformat=".0%")
    st.plotly_chart(_layout(figure=figure, title="Fraud rate by risk score band", height=360), width="stretch")
    st.dataframe(
        _percent(by_level, columns=["fraud_rate", "share_of_all_fraud"]),
        hide_index=True,
        width="stretch",
        column_config={
            "risk_level": "Risk level",
            "bookings": "Approved bookings",
            "frauds": "Chargebacks",
            "fraud_rate": st.column_config.NumberColumn("Fraud rate", format="%.1f%%"),
            "share_of_all_fraud": st.column_config.NumberColumn("Share of all fraud", format="%.0f%%"),
        },
    )
