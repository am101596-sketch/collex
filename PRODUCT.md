# Collex

<!-- impeccable:product-schema 1 -->

## Platform
web

## Users
Collections staff reviewing overdue invoices, account context, drafts, purchase-order cases, disputes, and write-offs in a daily workspace. Scope confirmed by the user.

## Product Purpose
Find small overdue balances that have not been followed up, prepare collection actions, and let a person review decisions.

## Operating Context
Python standard-library servers serve a local HTML/JavaScript workspace with separate approvals and optional agent desk services. The integrated layout uses ports 8090, 8092, and 8091 respectively. All fonts, scripts, and assets are bundled for offline operation.

## Capabilities and Constraints
Preserve the invoice data, Collex name, approval controls, offline operation, account grouping, dispute evidence, withdrawal reasons, and source-backed assistant. Financial actions remain owned by the existing approval backend. The exported dataset is synthetic; do not imply real customers or measured recovery.

## Brand Commitments
Use Linear's dark product workspace as the visual reference: a fixed left sidebar, compact breadcrumb header, neutral near-black surfaces, thin dividers, muted labels, restrained violet actions, and dense financial rows. Keep the Collex identity and actual invoice data. Workspace search and Ctrl/Cmd K navigate views and search invoices. The assistant opens on demand. Preserve review, dispute, purchase-order, write-off, and controlled-pilot workflows. On mobile, use a collapsible sidebar and a horizontally scrollable ledger with invoice identity pinned.

## Evidence on Hand
The user requested a very cute Fuggler-inspired chat character. A generated, transparent plush companion lives in app/assets/collex-companion.png; it appears within the assistant panel, separate from the invoice board.

README.md documents workflows. app/index.html implements the collector workspace. data/exports contains the synthetic invoice world. channel/dashboard.py implements approval actions.
