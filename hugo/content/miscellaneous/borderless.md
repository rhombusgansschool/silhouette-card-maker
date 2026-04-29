---
title: 'Borderless Templates'
weight: 1
---

Borderless templates artificially increase the maximum cutting area and maximum number of cards per sheet.

## How It Works

Silhouette Studio limits the minimum inset of registration marks to 10mm.

A smaller inset translates to more cutting area and more cards per sheet.

We cannot reduce the inset below the minimum inset, so instead, we use custom paper sizes in Silhouette Studio.

By adding 7mm to the paper width and height in Silhouette Studio, we can essentially trick the software into effectively using a 3mm inset.

## Benefits

The table below shows all possible borderless paper and card size combinations, the layout of the cards, and the improvement in card count.

| Format | `letter` | `tabloid` | `a4` | `a3` | `arch_b` |
|---|---|---|---|---|---|
| `standard` | 3x3 (+1) | 6x3 (+2) | 3x3 (+1) | N/A | N/A |
| `poker` | 3x3 (+1) | 6x3 (+2) | 3x3 (+1) | N/A | N/A |
| `bridge` | 3x3 (+1) | 7x3 (+5) | 5x2 (+1) | N/A | N/A |
| `american_mini` | 5x4 (+4) | 10x4 (+4) | 6x3 (+2) | N/A | N/A |
| `bridge_square` | 4x3 (+0) | 7x4 (+0) | 5x3 (+3) | N/A | N/A |
| `business` | 2x5 (+0) | 3x7 (+1) | 2x5 (+0) | N/A | N/A |
| `catan` | 3x3 (+0) | 7x3 (+0) | 5x2 (+0) | N/A | N/A |
| `credit` | 3x3 (+1) | 3x7 (+5) | 2x5 (+0) | N/A | N/A |
| `domino` | 6x2 (+2) | 9x3 (+7) | 6x2 (+2) | N/A | N/A |
| `domino_square` | 6x4 (+4) | 9x6 (+9) | 6x4 (+4) | N/A | N/A |
| `euro_business` | 3x3 (+0) | 3x7 (+0) | 2x5 (+1) | N/A | N/A |
| `euro_mini` | 5x3 (+3) | 5x6 (+3) | 4x4 (+4) | N/A | N/A |
| `japanese` | 3x3 (+1) | 6x3 (+2) | 3x3 (+0) | N/A | N/A |
| `jumbo` | 3x1 (+1) | 2x3 (+2) | 2x2 (+1) | N/A | N/A |
| `micro` | 8x4 (+4) | 12x6 (+9) | 6x6 (+6) | N/A | N/A |
| `mini` | 6x3 (+3) | 9x4 (+4) | 6x3 (+2) | N/A | N/A |
| `standard_double` | 2x2 (+0) | 3x3 (+1) | 2x2 (+0) | N/A | N/A |
| `tarot` | 2x2 (+0) | 6x2 (+2) | 4x1 (+0) | N/A | N/A |
| `70mm_square` | 3x2 (+0) | 5x3 (+0) | 4x2 (+2) | N/A | N/A |

`a3` and `arch_b` are not supported at the moment due to this [issue](https://github.com/Alan-Cha/silhouette-card-maker/issues/136).

## Usage

```sh
python create_pdf.py --borderless
```

Get your PDF at `game/output/game.pdf`.

Use the appropriate borderless cutting template in [cutting_templates/borderless/](https://github.com/Alan-Cha/silhouette-card-maker/tree/borderless_templates4/cutting_templates/borderless).

While placing the sheet onto the mat, maintain 7mm of clearance. In other words, instead of aligning the sheet with the grid on the mat, ensure that there is 7mm gap around the sheet.

If you encounter registration issues, you can also put extra paper behind the registration marks.
