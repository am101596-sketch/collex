# Collex workspace design

The visual reference is [Linear's product workspace](https://linear.app/): a near-black
canvas, compact sidebar, thin dividers, restrained typography and violet accents.
Collex retains its own identity, financial vocabulary, evidence and approval workflows.

## Tokens

| Role | Value |
| --- | --- |
| Canvas | `#111214` |
| Sidebar | `#0d0e10` |
| Surface | `#17181b` |
| Hover | `#202126` |
| Border | `#292a2f` |
| Strong border | `#3a3b43` |
| Primary text | `#ededf0` |
| Secondary text | `#a0a1aa` |
| Metadata | `#797b86` |
| Accent | `#817bf2` |
| Primary button | `#7169d7` |
| Approved / paid | `#8cc6a3` |
| Overdue / attention | `#e0b774` |
| Dispute status | `#b5a2ed` |
| Withdrawal / rejection | `#ee9b9d` |

Inter Variable is bundled in `app/assets/InterVariable.woff2` under the accompanying
SIL Open Font License. No remote fonts or assets are required. Use system monospace
for invoice identifiers and evidence references, and tabular numerals for amounts.
Titles are 23px, primary content 13–14px, secondary metadata 11–12px. Keep headings
medium weight; avoid decorative headlines and large promotional cards in working views.

## Layout

- The 224px left sidebar groups portfolio, review and collection workflows. At
  intermediate widths it is 200px. Its footer shows actual snapshot metadata.
- The 54px breadcrumb bar identifies the current view and exposes Ask Collex.
- The invoice board combines restrained totals, status tabs, search, owner filtering,
  and the complete export ledger. Headers stay visible during table scrolling.
- Review and account views pair a scrollable list with evidence and details.
- Pilot retains separate synthetic/real cohorts, measurements and evidence timelines.
- Ask Collex opens on demand. Above 1600px it occupies a dedicated 360px column;
  otherwise it overlays the right side. The plush companion stays within the panel
  as a small identity element.
- Below 760px, navigation is a collapsible drawer. Invoice identity stays pinned
  while the ledger scrolls horizontally. Detail and Pilot columns stack.
- Import and standalone approval screens share the same dark palette and local font.

## Interaction and accessibility

Workspace search opens from the sidebar or Ctrl/Cmd K. It jumps to views or searches
all invoices, clearing previous status and owner filters. Enter opens the first
result; arrow keys move between commands; Escape closes the native modal dialog.

Keep semantic controls, labels, keyboard focus outlines, reduced-motion support and
text accompanying status colors. The navigation drawer is removed from keyboard and
accessibility navigation while closed on mobile. Do not introduce dummy controls,
fabricated data, recovery claims or unverified model/connectivity claims.

All assets are served by the local Python application. Styling does not change
eligibility, approval authority, model selection or payment reconciliation.
