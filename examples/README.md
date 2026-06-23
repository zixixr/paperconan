# paperconan example — a worked walkthrough

This folder is a complete, runnable example: a **synthetic** paper's source data
with fabrication-style patterns planted on purpose, plus the report paperconan
produces from it.

> ⚠️ The data in [`demo_paper/`](demo_paper/) is fabricated for demonstration.
> No real paper, dataset, or person is involved.

---

## What's here

```
examples/
├── make_demo_data.py     # regenerates demo_paper/*.xlsx (annotated with what's planted)
├── demo_paper/           # the synthetic "paper" source data
│   ├── ED_Fig2_tumor_volume.xlsx
│   ├── ED_Fig4_qPCR.xlsx
│   └── audit/            # paperconan output (committed so you can preview it)
│       ├── scan.json
│       └── report.html   # ← open this in a browser
├── report-preview.png    # screenshot of report.html (GitHub doesn't render HTML)
└── README.md             # this file
```

## Preview without installing anything

Open [`demo_paper/audit/report.html`](demo_paper/audit/report.html) in a browser,
or look at the screenshot:

![paperconan report preview](report-preview.png)

## Run it yourself

```bash
pip install -e ..                       # install paperconan from this repo
paperconan demo_paper                   # writes demo_paper/audit/{scan.json,report.html}
open demo_paper/audit/report.html       # macOS; use xdg-open on Linux
```

To rebuild the synthetic data from scratch:

```bash
python make_demo_data.py demo_paper
```

---

## What paperconan finds (and what it means)

Running on this dataset with the default `review` profile surfaces **5 high** +
**5 medium** + **2 low** findings across 2 files.
Here's the guided tour — the order below mirrors how you should read the report.

### 1. Cross-sheet collision (read this first)

```
[high] cross_sheet_position_identical
  donor_A and donor_B share 22/36 (61%) decimal values at SAME (row,col)
```

`ED_Fig4_qPCR.xlsx` has two sheets — `donor_A` and `donor_B` — that are supposed to
be **independent donors**. 61% of the decimal values are byte-identical at the *same
cell position*. In the demo this is because `donor_B` was made by copying `donor_A`'s
`efficiency` / `input_ng` columns and nudging two cells. That "copy a sheet, tweak a
couple of values" move is the single most-investigated paperconan signal.

**How to verify in real life:** read the figure legend / Methods — is there a declared
shared control? If not, this warrants a question on PubPeer.

### 2. Reconstructed columns in the tumor data

```
[high] constant_offset    col[2] = col[1] + 120      (treat_volume = ctrl_volume + 120)
[high] identical_column   col[3] == col[1]           (ctrl_replicate is a verbatim copy)
```

In `ED_Fig2_tumor_volume.xlsx`, the "treated" group is exactly the control group plus
a constant, and a third column is a verbatim duplicate of the control. Real treatment
effects are not a fixed offset on every single animal.

### 3. Within-column fingerprints in qPCR

```
[low]    within_col_value_duplication   col[1] has value 1.0837 repeated 8/12 times
[high]   within_col_decimal_repetition  col[1]: 8/12 values share last-2 decimals '.37'
[medium] rounded_to_half_or_int         col[2]: 12/12 values end in 0 or 5
```

`rel_expr` reuses the exact value `1.0837` in two-thirds of "independent" samples, and
every `ct_value` lands on a 0/0.5 grid. Real measurements don't pile onto one value or
snap perfectly to a grid. The repeated-value finding is demoted in the default profile
because `rel_expr` looks like a normalized / fold-change column; rerun with
`--profile forensic` if you want the raw detector severity.

### 4. Medium and low findings — useful, but read with care

```
[low]    arithmetic_progression   col[0] = arithmetic progression, step=3
[medium] many_equal_pairs         tumor_length == tumor_width in 7/8 rows
[medium] small_diff_set / identical_after_rounding ...
```

The `day` column (0, 3, 6, … 21) is flagged as an arithmetic progression — but it's a
**legitimate time axis**, a textbook false positive. `tumor_length == tumor_width` is
suspicious but could happen if both are read off the same caliper image. This is exactly
why paperconan severity is **not** a misconduct verdict: a `high` can be benign (shared
control) and a `medium` can be the real tell. You still have to read the paper.

---

## The golden rule

paperconan output is a **statistical signal, not a verdict**. The report footer says
it, and so does this README: take findings to the original authors, PubPeer, the journal
ethics inquiry channel, or a research integrity office — never to social media as an
accusation. See [`../skills/paperconan/references/interpretation.md`](../skills/paperconan/references/interpretation.md)
for the full guidance and response templates.
