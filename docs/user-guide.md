# Vision Pharma plus: Staff User Guide

**Who this guide is for:** everyone who uses the system at work. Sales staff, pharmacists, storekeepers, buyers, accounts staff, managers, and administrators.

**What it covers:** every screen, every button, every word on screen, and what happens behind the scenes when you click. You do not need any technical knowledge to read it.

**How to use it:** Part 1 explains the ideas and words the system uses. Read it once. Part 2 goes through the screens one by one. Use it when you are on a screen and want to know what something does.

---

## Table of contents

**Part 1: Understanding the system**
1. What this system is
2. The words the system uses (glossary)
3. How the system thinks about stock: products, batches, and the ledger
4. How the system thinks about money
5. Who can do what (roles and permissions)
6. Language, theme, and getting around

**Part 2: Screen by screen**
7. Sign in
8. Dashboard
9. Catalogue: Medicines, Categories, Manufacturers, Units of measure
10. Inventory: Receive stock, Stock levels, Batches, Movements, Warehouses, Expiry, Reconciliation
11. Sales: New sale, Sales list, Sale detail, Returns
12. Invoicing: Invoices, Invoice detail, Payments, Declaring to the OBR
13. Purchasing: Purchase orders, New/Edit order, Order detail, Goods receipts, Supplier invoices, Supplier payments
14. Partners: Customers, Suppliers
15. Accounting: Financial overview, Expenses, Expense categories, Accounting reports
16. Reports
17. Administration: Users, Roles, Audit log
18. Notifications
19. My profile

**Part 3: Reference**
20. Every status word explained
21. Common error messages and what to do
22. Rules the system will never let you break
23. Everyday tasks, step by step

---

# PART 1: UNDERSTANDING THE SYSTEM

## 1. What this system is

Vision Pharma plus is the management system for a **wholesale pharmacy** (in French, *pharmacie de gros*) operating in Burundi.

A wholesale pharmacy is not a shop. The difference matters, and it shapes the whole system:

| A retail shop | This wholesale business |
|---|---|
| Sells single boxes to patients | Sells cartons to pharmacies, hospitals, clinics, and NGOs |
| Nobody tracks which box went to whom | Every unit must be traceable back to its production batch |
| One till, one till roll | Formal documents with legal numbering for the tax authority |

**Selling is not one thing.** Some customers settle at the counter; others are on account and pay in 30, 45, or 60 days. The system handles both, and which one you are doing decides what the customer walks away with:

| | **Cash sale** | **Credit sale** |
|---|---|---|
| When the customer pays | Now, at the counter | Later, on agreed terms |
| What they are given | A **receipt** | An **invoice** |
| What it leaves behind | Nothing owing | An open balance, settled by a payment later |

A cash sale is not invoiced and a credit sale is not receipted — a sale produces one closing document, not two, so it is never counted twice in the reports. The exception is a cash customer who asks for an invoice anyway, or a tax rule that requires one: the invoice is then raised from the same sale and linked to the receipt, so the pair still reconciles. See section 11.1.

Because of all this, the system does five jobs:

1. **Keeps the product list** (the catalogue): what you sell, at what price.
2. **Tracks stock by batch**. Not just "we have 500 boxes", but "we have 200 from batch A which expires in March and 300 from batch B which expires in September".
3. **Handles selling**, from picking products for a customer, to producing the receipt or invoice, to collecting the money.
4. **Handles buying**, from raising an order with a supplier, to getting it approved, to receiving the goods into stock.
5. **Records everything permanently**, so that if a regulator, an auditor, or the tax office asks a question, there is a complete and unchangeable answer.

### The three rules that shape everything

Almost every unusual behaviour in this system traces back to one of these three:

**Rule 1. Traceability.** If a manufacturer recalls a batch of medicine, the business must be able to name every single customer who received a unit from that batch, within minutes. This is why the system insists on a batch number and expiry date for every delivery you receive, and why it records which batch each sale came from.

**Rule 2. Tax compliance.** The Burundian tax authority (**OBR**, *Office Burundais des Recettes*) requires invoice numbers with no gaps. If invoice FAC-2026-000147 and FAC-2026-000149 exist but 000148 does not, that looks like a hidden sale. The system therefore guarantees the numbering never skips.

**Rule 3. Two languages.** French is the default language, English is available. This is not just labels on buttons: invoices, emails, and reports all come out in the right language.

---

## 2. The words the system uses (glossary)

These are in alphabetical order. If a word on screen confuses you, look here first.

**Allocation (of a payment).** When a customer pays money, the system decides which invoices that money settles. That decision is called allocation. If a customer owes on three invoices and pays enough for two, the system allocates the payment to the two oldest.

**Audit log.** A permanent list of everything that happened in the system: who did it, when, from which computer, and what changed. Nothing can ever be edited or removed from it.

**Available (quantity available).** Stock you can actually sell right now. This is the quantity remaining minus anything reserved for a sale already in progress.

**Batch (in French, *lot*).** A quantity of a medicine that was manufactured together, at the same time, with the same expiry date, identified by a batch number printed on the box by the manufacturer. **The batch is the most important idea in this system** — section 3 explains it in full. Two boxes of the same medicine from different batches are treated as completely different stock, because they expire on different dates, cost different amounts, and because a recall affects one and not the other.

**Batch number (*numéro de lot*).** The manufacturer's code for a production run, printed on the carton. You copy it into the system exactly as printed; the system never generates one for you. A batch number that does not match the carton is useless in a recall, which is the whole reason it is recorded.

**Balance due.** How much money is still owed on an invoice. Total amount minus what has been paid.

**BIF.** Burundian Franc, the currency. It has no coins or subdivisions in everyday use, so invoices always show whole numbers.

**Cash sale.** The customer pays at the time of sale. Compare with credit sale.

**Cold chain.** Products that must be kept refrigerated (2 to 8 degrees) from the factory to the customer. A warehouse can be marked as cold chain capable.

**Controlled substance.** A medicine with legal restrictions on how it is stored, sold, and recorded. Marked with a tick box on the medicine record.

**Credit limit.** The maximum amount a customer is allowed to owe at any one time. If a new sale would push them over this, the system refuses the sale.

**Credit note (in French, *note de crédit*).** A formal document that cancels part or all of an invoice. Because a posted invoice can never be edited or deleted, a credit note is the only correct way to reverse a charge.

**Credit sale.** The customer takes the goods now and pays later, according to agreed payment terms. Requires them to have a credit limit and a NIF.

**Dead stock.** Stock that has not moved in a long time. Money sitting on a shelf.

**Discount.** A reduction in price, entered as a percentage. Applying a discount is a restricted action, and a manually entered discount can never exceed 10%. See section 4.

**Dosage form.** The physical shape of the medicine: tablet, capsule, syrup, injection, cream, and so on.

**Expense.** An operating cost of running the business — rent, salaries, electricity, fuel, freight. Recorded in the Accounting module. Distinct from a supplier payment, which settles a bill for goods. See section 15.

**Expense category.** The heading an expense is grouped under for reporting. See section 15.3.

**Expiry date.** The date after which a batch may not be sold or used. The system will physically prevent you from selling expired stock.

**FEFO, or First Expired, First Out.** The rule the system uses to decide which batch to sell from. It always picks the batch that expires soonest, so that stock is used before it goes out of date rather than being forgotten behind newer boxes. If two batches expire on the same day, the one received first is used first.

**Goods receipt.** The act of booking a delivery into stock, done on the **Receive stock** screen. This is where batch numbers and expiry dates get entered, and the only place stock enters the system. A delivery may arrive against a purchase order or without one.

**Opening stock (opening balance).** The stock you already held on the day the system went live. It is entered on the Receive stock screen with the *Opening stock* box ticked, so the system records it as a starting count rather than as a purchase from a supplier.

**Landed cost.** What a product really cost you, including not just the supplier's price but also freight, customs duty, and other charges spread across the delivery. This is the honest cost used for valuing your stock.

**Reference cost.** The typical purchase price recorded against a product in the catalogue. It is used to work out the margin shown on the product page, to warn you when you price below cost, and to pre-fill the cost when you raise an order or receive a delivery. It is a guide, not a valuation: your stock is worth what its batches cost, not what this field says. Contrast **supplier unit cost** and **landed cost**.

**Supplier unit cost.** The price a particular supplier charges per unit on a particular purchase order. This is a real price on a real document, unlike the catalogue's reference cost, and it is what the delivered batch is valued at before freight and duty are added.

**Ledger (stock ledger).** The permanent, running list of every single stock movement ever made. Like a bank statement for stock. Nothing is ever deleted from it; mistakes are corrected by adding a new correcting entry.

**Movement.** One entry in the stock ledger: a receipt, a sale, a transfer, an adjustment, and so on. Each has a plus or minus quantity.

**NIF (*Numéro d'Identification Fiscale*).** A customer's tax identification number, issued by the OBR. A customer buying on credit must have one, because their invoice is a tax document.

**OBR (*Office Burundais des Recettes*).** The Burundian tax authority.

**Declaration (of an invoice).** Sending an invoice electronically to the OBR. It happens automatically in the background a few minutes after you post, so it never holds up a sale. See section 12.4.

**Fiscal signature.** The code identifying an invoice to the OBR, made from the pharmacy's NIF, the OBR's identifier for this software, the invoice date and the invoice number. Worked out on this computer at the moment you post, with no internet needed.

**Fiscal status.** Whether an invoice has reached the OBR yet. Separate from the invoice's ordinary status: one is about the tax authority, the other about the money.

**Payment terms.** When a customer must pay. "Cash" means immediately. "NET 30" means within 30 days of the invoice date. Also available: NET 7, NET 15, NET 45, NET 60, NET 90.

**Posted (an invoice).** An invoice that has been finalised and issued. Once posted, it can never be edited or deleted. It is a legal document.

**Purchase order (PO, in French *bon de commande*).** A formal document you issue to a supplier to request and authorise goods. It is raised in the system, approved by someone other than the person who raised it, sent to the supplier, and then used as the basis for receiving the delivery.

**Quarantined.** A batch that is being held and cannot be sold, usually pending a quality check.

**Reconciliation.** A check that the system's stock figures agree with the permanent ledger. A mismatch is a serious problem, not a rounding issue.

**Recall.** When a manufacturer or regulator declares a batch unsafe. The business must find and contact everyone who received it.

**Reorder level.** The stock quantity at which the system starts warning you to buy more.

**Reserved.** Stock that is spoken for by a sale in progress but has not yet physically left. It counts as on hand but not as available.

**Safety stock.** A buffer quantity you aim to always keep, so a delay from a supplier does not leave you at zero.

**Separation of duties.** A control that stops one person doing both halves of a risky transaction. In this system: the person who raises a purchase order cannot be the person who approves it; the person who records a supplier invoice is not necessarily the one who may pay it; and recording an expense is a different permission from approving it.

**Statement (customer statement).** A summary of everything a customer has bought and paid, and what they still owe.

**Supplier.** A company you buy from. Must be approved before it can be used on a purchase order.

**Supplier invoice.** A bill received *from* a supplier — what you owe them. Not to be confused with an invoice, which is what a customer owes you. See section 13.6.

**Supplier payment.** Money paid out to a supplier, allocated across their open invoices. See section 13.7.

**Payment receipt.** The document acknowledging that a customer's invoice has been settled in full. Issued automatically the moment the balance reaches zero. Distinct from a sales receipt, which closes a cash sale.

**Unit of measure.** How you count a product: each, box, carton, bottle. Maintained on its own screen — see section 9.6.

**VAT (in French, *TVA*).** Value added tax. Some medicines are exempt; each product carries its own rate.

**Warehouse.** A physical place where stock is kept. The system can handle several.

**Write off.** Removing stock from the books because it is damaged, expired, or lost.

---

## 3. How the system thinks about stock

This section explains the single most important behaviour of the system. If you understand this, most other things make sense.

### A product is not stock

These are two different things, and keeping them separate is what makes everything else work.

| | What it is | Where it lives |
|---|---|---|
| **Product** (medicine) | *"We sell Amoxicillin 500mg."* A definition: name, category, price, storage rules, reorder level. | Catalogue |
| **Batch** | *"We hold 200 units of lot AMX-2405-A expiring March 2027 in the main store."* A fact about a physical carton. | Inventory |

Creating a product does **not** create stock. A brand-new product has zero quantity and no batches, and that is correct — you have told the system you sell it, not that you have any.

**Why they are separate.** You usually need the product before you have the stock. You cannot put an item on a purchase order until it exists in the catalogue, and that happens weeks before the goods arrive. If cataloguing demanded a batch number, you would have to invent one — and an invented lot number is worse than none at all, because it looks real until the day a recall depends on it.

So: **the catalogue is where you describe what you sell. The Receive stock screen is where you record what you physically have.** The batch number and expiry come from the carton, at the moment the carton is in your hands.

The connection runs from stock back to the product: every batch belongs to a product, so a product's page shows its live stock, its batches, and their value, and the reorder level you set on the product is what triggers its low-stock warning.

### What a batch actually is

A **batch** (in French, *lot*) is a quantity of one medicine that the manufacturer produced in a single run. Everything in that run shares one expiry date, and the manufacturer prints a **batch number** on every carton to identify it — something like `AMX-2405-A`.

You do not invent this number. **You copy it off the carton.** It is the manufacturer's identifier, not yours.

In this system, a batch is the combination of five things:

| | Example |
|---|---|
| Which **product** | Amoxicillin 500mg |
| Which **batch number** | AMX-2405-A |
| Which **expiry date** | March 2027 |
| Which **warehouse** | Main store |
| What it **cost** | 1,150 BIF per unit landed |

Change any one of the first four and it is a **different batch**, tracked separately, even if it is the same medicine sitting on the same shelf.

### Where batches come from

**Only one place: the Receive stock screen.** A batch is created the moment you book a delivery in and type the lot number off the carton.

Batches are never created by the catalogue, never generated automatically, and never invented by the system. This is deliberate and it is the single most important rule in the application:

> **If the batch number in the system does not match the number printed on the box, the batch is worthless.**

Here is why. When a manufacturer recalls lot `AMX-2405-A`, the regulator gives you that exact code. You type it into the system and it must tell you: how many units you still hold, which warehouse they are in, and — critically — **which customers received units from that lot**. If someone had typed `N/A`, or the system had auto-generated `BATCH-00017`, that search returns nothing. You would have to quarantine everything and telephone every customer, and you would still not be able to prove to the regulator who got what.

That is the entire reason the batch number field exists, and the reason it is never optional and never filled in for you.

### Receiving the same batch number twice

If a second delivery brings more of a lot you already hold — same product, same batch number, same expiry, same warehouse — the system **adds to the existing batch** rather than creating a duplicate. The cost is averaged across the old and new units, weighted by quantity, so your valuation stays honest.

But if the batch number matches and the **expiry date does not**, the system **stops you**. That means one of two things, and both need a person to look:

- Someone mistyped a date, or
- The manufacturer has reused a lot number across two production runs.

Either way, silently accepting it would leave one batch carrying an expiry that contradicts the carton. So it refuses and tells you.

### A batch has a status

A batch is not simply present or absent. It carries a status that governs whether it can be sold: **Active**, **Quarantined**, **Expired**, **Damaged**, **Recalled**, **Depleted**, or **Disposed**. Only Active batches that have not passed their expiry date can be sold. Full table in section 10.3.

The status changes on its own where the system can tell: a batch that reaches zero becomes Depleted, and one that receives a customer return goes back to Active.

### Stock is counted by batch, never just by product

When you ask "how much Amoxicillin 500mg do we have?", the system does not hold one number. It holds a list:

| Batch number | Expires | Quantity | Landed cost each |
|---|---|---|---|
| AMX-2405-A | March 2027 | 200 | 1,150 BIF |
| AMX-2411-C | September 2027 | 300 | 1,210 BIF |
| AMX-2502-B | January 2028 | 450 | 1,180 BIF |

Total: 950 units. But that total is the least useful thing on the table, and the system never treats it as one pile of 950, because:

- **The March batch must be sold first.** See FEFO below.
- **When March arrives, only the first 200 become worthless**, not all 950.
- **If batch AMX-2411-C is recalled**, only those 300 units and the customers who received them are affected. The other two batches keep selling normally.
- **They cost different amounts.** Your stock here is worth 200 × 1,150 + 300 × 1,210 + 450 × 1,180, not 950 multiplied by some average. Each batch is valued at what that particular delivery actually cost.

This is why the system will not let you type "we have 950 Amoxicillin". It only ever accepts stock one batch at a time, with the lot number and expiry that belong to it.

### When you sell, the system picks the batches for you

You never choose a batch when selling. You enter the product and the quantity, and the system applies **FEFO**: it takes from the batch that expires soonest, then moves to the next one when that batch runs out.

If a customer orders 350 units of the Amoxicillin above, the system takes:
- 200 from AMX-2405-A (expires soonest, now finished)
- 150 from AMX-2411-C

Both batch numbers then appear on the invoice, so the customer has their own traceability record.

**Expired batches are invisible to selling.** It is not that selling them is discouraged. The system cannot see them as sellable stock at all. The same applies to quarantined, damaged, and recalled batches.

### The ledger is the truth, the balance is a convenience

Every time stock moves, the system writes a permanent line in the stock ledger with a plus or minus quantity. The current stock figure you see on screen is a running total kept for speed.

This design gives three things:

1. **You can answer questions about the past.** "What did we hold on 12 March?" is answerable exactly, because you can replay the ledger up to that date.
2. **You can trace a recall.** Follow the ledger from a batch to every sale that consumed it, to every customer.
3. **You can detect errors.** The Reconciliation screen re-adds the whole ledger and compares it to the running total. If they disagree, something is wrong and needs investigating.

### Mistakes are corrected by adding, never by erasing

If a stock movement was wrong, you do not delete it. You make a new adjustment that corrects it, with a reason. Both entries stay in the ledger forever. This is the same discipline a bank uses: you never rub out a line on a bank statement, you add a correcting transaction.

The same principle applies to invoices. A posted invoice is never edited. You issue a credit note against it.

---

## 4. How the system thinks about money

### Whole francs on documents, fractions behind the scenes

The BIF has no small change in circulation, so every printed invoice shows whole numbers. But real wholesale costs carry fractions: 10,000 tablets at 12.4567 BIF each is a genuine figure, not a rounding error.

So the system calculates with four decimal places internally and rounds once, at the point where the document is produced. This is why the total on an invoice always exactly equals the sum of its lines. An auditor checks this, and it will always add up.

### VAT is already inside the price you enter

Catalogue prices are **VAT-inclusive**. Before a line is calculated, the system separates the tax that is already in the price:

- **Tax-exclusive base** = price ÷ (1 + VAT rate)
- A product at 1,180 BIF with 18% VAT has a base of 1,000 and carries 180 of VAT.
- A VAT-exempt product has nothing to separate, so its base equals its price.

This happens once, at the moment a catalogue price becomes a sale line. Everything after that — discounts, totals, the tax summary, and what is filed with the OBR — works on the tax-exclusive base, which is what the tax authority requires.

### Discount comes first, then VAT

The order is fixed and cannot be changed:

1. Start with quantity multiplied by the tax-exclusive unit price. This is the **gross line**.
2. Subtract the discount percentage. This gives the **net line**.
3. Apply VAT to the net line.
4. The result is the **line total**.

Worked example: 100 units of a product priced at **1,180 BIF including VAT**, with a 10% discount and 18% VAT.

| Step | Calculation | Result |
|---|---|---|
| Tax-exclusive price | 1,180 ÷ 1.18 | 1,000 |
| Gross | 100 × 1,000 | 100,000 |
| Discount | 10% of 100,000 | −10,000 |
| Net | | 90,000 |
| VAT | 18% of 90,000 | +16,200 |
| **Line total** | | **106,200** |

Without the discount, the same 100 units would total exactly 118,000 — the shelf price times the quantity, which is the point of pricing this way.

Applying VAT before the discount would charge the customer VAT on money they never paid. That is a tax compliance error, not a matter of preference, which is why the order is not adjustable.

### There is a hard ceiling on discounts

A manually entered discount cannot exceed **10%**. Try to enter more and the sale is refused: *"A discount of 15% exceeds the maximum of 10%."*

**The ceiling is absolute.** There is no override permission and nobody can authorise more at the counter — not a manager, not an administrator. A limit that a sufficiently senior person can lift is not a limit, it is a speed bump. Changing the figure is a system configuration decision, made once for the whole business, not a per-sale one.

Two things follow from this that are worth knowing:

**Only people with discount authority can discount at all.** That is Store Managers and administrators. A pharmacist or technician who tries is told they do not have permission, rather than being invited to try a smaller number that would fail for the same reason.

**A customer's standing discount is not capped.** The ceiling applies to discounts *typed at the counter*. A discount that comes from the customer record is a contractual rate agreed when the account was opened, governed by who may edit customers, and is not re-argued on every sale. A customer whose negotiated rate is 15% goes on being billed at 15%.

### Totals update as you type

On the New sale screen, the totals recalculate live, using exactly the same method the server will use. What you see before you click Confirm is what the invoice will say. The server still does the final calculation and remains the authority, but there are no surprises.

---

## 5. Who can do what (roles and permissions)

### How access works

Every action in the system requires a specific **permission**. A permission is a named capability, like "create a sale" or "approve a purchase order". There are 75 of them.

You are not given permissions one at a time. You are given a **role**, and the role carries a set of permissions. Roles are set up so they match real jobs.

**If you do not have permission for something, you will not see the button.** Menu items you cannot use are hidden, buttons you cannot press are not shown, and fields you may not change are greyed out with an explanation. The screen adapts to your job.

Importantly, hiding a button is only a convenience. The real check happens on the server. Nobody can get around a permission by finding a clever route through the screens.

### The six standard roles

**Pharmacy Technician.** The entry-level operational role. Day-to-day selling and looking things up.

Can: view medicines, view stock and batches, issue stock, create and view sales, create, view and print invoices, view customers, see the dashboard.

Cannot: apply discounts, sell on credit, change prices, approve anything, receive goods, see profit margins, or see the value of the inventory.

**Pharmacist.** Everything a technician can do, plus clinical and commercial authority.

Adds: create and edit medicines, manage categories and manufacturers, receive stock, adjust stock, dispose of expired stock, transfer stock between warehouses, edit and cancel sales, **sell on credit**, process returns, post invoices, email invoices, record payments, issue credit notes, create and edit customers, view customer statements, view suppliers, raise purchase orders and submit them for approval, view supplier invoices, record expenses, and view inventory and sales reports.

Cannot: **apply discounts**. Discounting is management authority and sits with the Store Manager and the administrator, not the counter. A pharmacist runs the counter day to day and books the small operating costs that arise there, but approving those costs and seeing the consolidated financial picture stay with management.

**Inventory Officer.** The warehouse role. Focused entirely on physical stock.

Can: view medicines, view stock and batches, receive stock, issue stock, transfer stock, adjust stock, view purchase orders, **receive goods**, view suppliers, see the dashboard and inventory reports.

Cannot: sell anything, touch prices, or approve anything.

**Store Manager.** Everything an Inventory Officer can do, plus oversight.

Adds: approve stock adjustments, dispose of stock, **see inventory valuation**, manage warehouses, create and edit purchase orders, **approve purchase orders**, cancel orders, record and view supplier invoices, **record and reverse supplier payments**, create and edit suppliers, view sales, **see profit margins**, **apply discounts**, **re-declare an invoice to the OBR**, record, edit and **approve expenses**, manage expense categories, **see the financial overview and accounting reports**, view sales and financial reports, and export data.

> **Worth planning for.** A Store Manager can both raise and approve purchase orders, but never the same one — the system refuses self-approval. If you have only one Store Manager, every order they raise needs a second person with approval authority, or procurement stalls. Make sure at least two people can approve, or that an administrator is available to.

**Auditor.** Read only, by design. This role can look at everything and change nothing.

It has every "view" permission in the system, plus the audit log, audit verification, audit export, all reports including compliance, inventory valuation, profit margins, and customer statements.

It has zero write permissions. This is not a policy that someone remembered to apply; the role is built from read-only capabilities so that a write permission cannot be added by mistake. An auditor who could alter records would defeat the purpose of the role.

**System Administrator.** Full access, including creating users, assigning roles, configuring numbering, and managing announcements.

### Roles build on each other

Two of the roles inherit from another:
- Pharmacist includes everything a Pharmacy Technician has.
- Store Manager includes everything an Inventory Officer has.

Auditor and System Administrator stand alone.

### Sensitive permissions

Some permissions are marked sensitive because they involve money, stock quantities, or access. These include: deleting a medicine, changing prices, adjusting stock, disposing of stock, viewing valuation, overriding FEFO, cancelling a sale, selling on credit, applying discounts, overriding a credit limit, processing returns, viewing margins, posting or cancelling invoices, recording payments, issuing credit and debit notes, approving or cancelling purchase orders, recording supplier invoices, recording and reversing supplier payments, editing, deleting and approving expenses, viewing the financial overview and accounting reports, deleting a customer or supplier, setting credit limits, financial and compliance reports, exporting data, and all user and role administration.

Actions using a sensitive permission are recorded prominently in the audit log.

### You can be given more than one role

Your permissions are then everything from all your roles added together. Your profile page shows which roles you hold.

---

## 6. Language, theme, and getting around

### The screen layout

Every page after sign-in has the same three parts.

**The sidebar (left).** Your menu. It is grouped into sections: Dashboard, Catalogue, Inventory, Sales, Purchasing, Partners, Reports, Administration. You only see sections containing at least one item you have permission to open, so a technician never sees an Administration heading they cannot use.

**Booking in a delivery is under Inventory → Receive stock**, at the top of that group, not under Purchasing. It sits there because it is the task you arrive with when goods are on the counter, and because deliveries do not always come against a purchase order.

At the bottom of the sidebar is your name, your email, and the Sign out button. Clicking your name opens your profile.

On a computer you can collapse the sidebar to a narrow strip of icons using the arrow button at the top, which gives more room to wide tables. Hovering an icon shows its name. Your choice is remembered.

On a phone or tablet the sidebar is hidden and opens with the menu button in the top left. Press Escape or tap outside it to close.

**The top bar.** Shows the name of the section you are in, so you always know where you are even after scrolling. On the right:
- **Theme button.** Switch between light, dark, and following your device setting.
- **Language button.** Switch between French and English.
- **Bell icon.** Notifications. A red number shows how many you have not read. It refreshes every minute.

**The main area.** The page content.

### Switching language

Click the language button and choose Français or English. The change is instant. You do not sign out, and you do not lose what you were doing.

The change is also saved to your account, which means invoices you generate and emails the system sends you will come in your chosen language too, not just the screen.

### Translating what a colleague wrote

Switching language changes the system's own labels, but it cannot change words a colleague typed. Notes, cancellation reasons, and quality comments are written in whichever language that person works in, and the person reading them later may not share it.

So translation is offered where you are **reading**. Wherever free text was typed by a user, a small **Translate** control sits beside it. Click it and a translation appears; click again to hide it. Nobody has to translate anything before saving, and the work is only done when someone actually needs it.

Two things about this are deliberate:

**The original is never replaced.** It stays on screen with the translation shown alongside. Several of these fields — cancellation reasons, quality notes, credit overrides — are audit evidence, and the words the writer chose *are* the record. A translation is a reading aid, not a replacement.

**The translation is always labelled as machine-produced.** Someone deciding whether to accept a returned batch needs to know they are reading a machine's rendering of a colleague's note rather than the colleague. Nothing you translate is saved back to the record.

The same machine translation fills in the second language when you type a category, manufacturer, or unit name in only one. There it *is* saved — as a starting point you can correct by editing.

### How lists work

Most screens are lists, and they all behave the same way.

- **Search box.** Type to filter. It waits until you stop typing before searching, so it does not stutter.
- **Filter dropdowns.** Narrow the list by status, type, warehouse, and so on.
- **Clear filters.** Appears when filters are active. Resets everything.
- **Pagination.** Page controls at the bottom, with the total count.
- **Clicking a row.** On most lists, this opens the full record.

Your search and filters are stored in the web address. This means you can refresh the page without losing your view, bookmark a filtered list, or send the link to a colleague and they will see the same view.

### Feedback messages

- **Toast.** A small box that slides in briefly at the corner. Green for success, red for failure.
- **Alert.** A coloured panel inside the page. Red is an error, orange is a warning, blue is information.
- **"Before you can continue".** A list explaining exactly why a button is disabled. This appears on the New sale, Receive stock, and New purchase order screens, where several things must be right before you can submit.

---

# PART 2: SCREEN BY SCREEN

## 7. Sign in

**Address:** `/login`
**Who sees it:** everyone, before signing in.

The only page you can reach without an account.

### What is on the screen

The company logo, and a card with two fields.

| Field | Notes |
|---|---|
| **Email address** | Your work email. This is your username. The cursor starts here. |
| **Password** | Hidden as you type. |

Below: the **Sign in** button, and a line reading *"Forgotten your password? Contact your administrator."*

In the top right, the theme and language buttons work before you sign in, so you can read the login page in your language.

### Password recovery

There is no self-service password reset. If you have forgotten your password, an administrator resets it for you and gives you a temporary one, which you must change the first time you sign in.

### If sign-in fails

The message tells you which of three different things went wrong. This distinction matters, so read it.

**"Incorrect email address or password."** One of the two is wrong. The system deliberately does not tell you which, because saying "that email exists but the password is wrong" would let a stranger discover who has accounts here.

**"Too many failed attempts. Try again in a few minutes."** Your account is temporarily locked after five failed attempts. Wait a few minutes. If you are sure of your password, contact your administrator, because someone else may have been trying to get into your account.

**"This account is suspended. Contact your administrator."** Your account has been deliberately switched off by an administrator. Waiting will not help. Contact them.

### What happens behind the scenes

Your password is never stored anywhere in a readable form. Every sign-in attempt, successful or failed, is written to the audit log with the date, time, and the computer's network address.

You stay signed in as you work. Sessions renew quietly in the background, so you will not be thrown out in the middle of a sale. If you are away for a long time, you may be asked to sign in again.

---

## 8. Dashboard

**Address:** `/dashboard`
**Permission needed:** view dashboard (all standard roles have it)
**What it is for:** the first screen after signing in. A summary of the whole business right now.

The date the figures were calculated appears under the heading.

### The tiles

Up to eleven tiles across the top. Two are only visible if you have the right permission, so most people see nine.

| Tile | What it means | Colour |
|---|---|---|
| **Inventory value** | Total value of all stock you hold. **Only visible with the "view valuation" permission** (Store Manager, Auditor, Administrator). | Green |
| **Active products** | How many products are currently in the catalogue. Underneath, the number of batches. | Plain |
| **Low stock** | Products at or below their reorder level. Time to buy more. | Orange if above zero |
| **Out of stock** | Products with nothing left at all. You cannot sell these. | Red if above zero |
| **Expiring soon** | Batches expiring within 90 days. | Orange if above zero |
| **Expired batches** | Batches already past their expiry date. These cannot be sold and need disposing of. | Red if above zero |
| **Today's sales** | Money taken today, plus the number of transactions. | Plain |
| **This month's sales** | Money taken this month, plus transaction count. | Plain |
| **Outstanding invoices** | Total still owed to you across all unpaid invoices, plus how many. | Plain |
| **Overdue invoices** | The part of that which is past its due date. Chase these. | Red if above zero |
| **This month's margin** | Profit this month. **Only visible with the "view margin" permission** (Store Manager, Auditor, Administrator). | Green |

If you do not have the valuation or margin permission, those tiles are not merely hidden from your screen; the figures are never sent to your computer at all.

### Choosing the period

Buttons at the top set the period the sales figures cover: **Today**, **This month**, **Last 30 days**, or **This year**. For anything else, set your own **From** and **To** dates. **Reset** puts it back to the default.

When you pick a period other than the default, the sales and margin tiles relabel themselves — "Sales for period", "Margin for period" — so a figure on screen always says which window it belongs to.

> **Not everything follows the date filter, and this is deliberate.** The screen tells you so: *"Stock and receivables are current positions and do not follow the date filter."*
>
> Sales and margin are **flows** — money that moved during a period, so a date range makes sense of them. Stock levels, expiring batches, and outstanding invoices are **positions** — what is true right now. There is no such thing as "out of stock last March" that helps you today, and showing an old receivables figure next to a live one is how people chase debts that were already paid.

### The tiles are clickable

Most tiles are links to the list they summarise. Clicking **Out of stock** takes you to the stock list already filtered to what has run out, rather than leaving you to rebuild that filter by hand. The whole tile is the target, not a small link underneath.

### Revenue trend

A line chart of daily revenue over the last 30 days. Hover over any point to see the exact figure for that day. If there were no sales in the period, it says so instead of drawing an empty chart.

### Top customers

Your ten biggest customers by revenue, with their customer code and total.

### Top selling products

Your ten best sellers, with quantity sold and revenue.

### Reading the dashboard each morning

A practical routine:
1. **Out of stock** above zero? Find out what and order it.
2. **Expired batches** above zero? These need removing from stock today.
3. **Expiring soon** above zero? Look at the expiry report and decide whether to run a clearance.
4. **Overdue invoices** above zero? Start chasing.
5. **Low stock**? Plan the next purchase order.

---

## 9. Catalogue

### What the catalogue is — and what it is not

**The catalogue is a list of what you sell. It is not a list of what you have.**

This trips up almost everyone at first, so it is worth being blunt about it. Creating a medicine here does **not** put any stock on your shelf. It does not create a batch. It does not record an expiry date. After you save a new medicine, the system holds **zero** of it, and that is correct — not a mistake, not an incomplete setup, and not something you need to fix.

Think of the medicine page as the **product's identity card**:

| The catalogue answers | The catalogue does **not** answer |
|---|---|
| What is this medicine called? | How many do we have? |
| What category and dosage form? | Which batches are on the shelf? |
| What do we charge for it? | When do they expire? |
| How should it be stored? | Which warehouse are they in? |
| When should we reorder? | What did this particular delivery cost? |

Everything in the right-hand column belongs to a **batch**, and batches are created on the **Receive stock** screen (section 10.1) when the goods physically arrive.

### Why it works this way

Because you almost always need the product before you have the stock.

To put an item on a purchase order, it must already exist in the catalogue — and that happens days or weeks before the delivery turns up. If the catalogue demanded a batch number and expiry, you would be stuck: you cannot read a carton that is still at the supplier's warehouse. The only way out would be to type something made up, and **an invented batch number is worse than none at all**, because it looks perfectly real right up until the day a recall depends on it.

So the system splits the job in two:

```
CATALOGUE                          RECEIVE STOCK
"We sell Amoxicillin 500mg"        "We hold 200 of lot AMX-2405-A,
Name, price, storage, reorder       expiring March 2027, in the main
level. No quantities.               store." Batch, expiry, quantity.

        created first                      created when goods arrive
```

### They are still connected

The catalogue is not cut off from your stock — the link simply runs the other way. Every batch belongs to a product, so:

- A medicine's **detail page** shows its live stock, all its current batches, and what they are worth. That page is where you look to answer "how much do we have?"
- The **reorder level** you set on the product is what makes it appear as low stock.
- The **expiring soon (days)** setting on the product controls when its batches start raising alerts.
- The **storage conditions** you set follow the product into every batch of it.

What you cannot do is type a quantity into the catalogue. Stock only ever enters through Receive stock, where the carton is in front of you.

### A worked example

You start selling a new product, Furosemide 40mg:

1. **Catalogue → New medicine.** Enter the name, category, unit, dosage form, cost and price. Save. *You now have a product with zero stock. This is normal.*
2. **Purchasing → New purchase order.** Order 500 units from an approved supplier. Get it approved and sent.
3. **The delivery arrives.** Inventory → Receive stock. The lot number on the carton reads FUR-2410-A, expiring October 2026. Type both, enter 500.
4. **Now you have stock.** The medicine's detail page shows 500 units in one batch, and it is sellable.

Steps 1 and 3 are separate on purpose. Step 1 could have happened three weeks before step 3.

---

The catalogue also holds the reference lists that organise your products: categories, manufacturers, and units of measure.

### 9.1 Medicines list

**Address:** `/catalog/medicines`
**Permission needed:** view medicines

The list of products. Columns include the product code, name, category, manufacturer, prices, and status. Search by name, code, generic name, or brand.

**New** (top right) appears if you have permission to create medicines. Clicking a row opens the product.

### 9.2 New medicine / Edit medicine

**Address:** `/catalog/medicines/new` and `/catalog/medicines/[id]/edit`
**Permission needed:** create or edit medicines (Pharmacist and above)

> **This form describes a product. It does not add stock.**
> There is no batch number and no expiry date on it, and that is deliberate — you may not have the carton yet. Saving creates the product with zero stock, ready to be ordered and received. Stock arrives on the **Receive stock** screen (section 10.1).

The same form is used for creating and editing, so the two can never disagree. It is split into four cards.

**Identification**

| Field | Required | Meaning |
|---|---|---|
| Name | Yes | The commercial name as you use it. Minimum 2 characters. |
| Generic name (INN) | No | The international non-proprietary name, the scientific name of the active ingredient. Paracetamol is the INN; Panadol is a brand. |
| Brand | No | Manufacturer's trade name. |
| Strength | No | For example "500mg" or "250mg/5ml". |
| Dosage form | Yes | Tablet, capsule, syrup, suspension, injection, infusion, cream, ointment, drops, suppository, inhaler, powder, solution, medical device, consumable, other. |
| Pack size | No | What is in one sales unit, for example "Box of 100". |
| Category | Yes | Which category it belongs to. Set these up on the Categories screen first. |
| Unit | Yes | How you count it: each, box, carton. |
| Manufacturer | No | Who makes it. |

The **product code** is not on this form. The system allocates it automatically when you save.

**Pricing and VAT**

> **Selling prices are entered VAT-inclusive (TTC).** Type the price the customer actually pays at the counter — the price on the shelf edge. Do not subtract VAT yourself; the system works out the tax-exclusive base and shows it under the field as you type.

| Field | Required | Meaning |
|---|---|---|
| Reference cost (BIF) | Yes | What you normally pay for one unit, **excluding VAT**. |
| Selling price (BIF) | Yes | Your standard price, **VAT included**. |
| Wholesale price (BIF) | No | An alternative price for large customers, **VAT included**. |
| VAT rate % | Yes | The tax rate for this product. |
| VAT exempt | Tick box | Tick if the product carries no VAT. |

Reference cost is the one price on this form that excludes VAT, because it is what your supplier charges you before tax. The two selling prices include it.

> **Reference cost is not what your stock is worth.** It is a typical figure used for three things: working out the margin shown on the product page, warning you if you price below cost, and pre-filling the cost when you raise a purchase order or receive a delivery. What your stock is actually valued at is the cost of each batch, taken from the delivery it arrived on. Two batches of the same product bought at different prices keep their own costs. Changing the reference cost here never re-values stock you already hold.

Worked example: a product sold at **180 BIF** with 18% VAT. You type 180. The system stores a tax-exclusive base of 152.5424 and records 27.4576 of VAT, and the customer is billed exactly 180. A VAT-exempt product has no tax to separate, so what you type is what is stored.

If you set a selling price below the reference cost, the system **warns you but allows it**. This is deliberate: selling below cost is a legitimate business decision when clearing short-dated stock, so the system flags it rather than blocking it. The comparison is made after VAT is removed, so that a thin margin is not hidden by the tax.

**Stock settings**

| Field | Meaning |
|---|---|
| Reorder level | When stock reaches this, the system flags it as low and warns you. |
| Safety stock | The buffer you aim to always keep. |
| Expiring soon (days) | How many days before expiry the system should start alerting on batches of this product. |
| Storage conditions | Required. Ambient (15–25°C), Cool (8–15°C), Refrigerated (2–8°C), Frozen, Protect from light, or Keep dry. |

**This form does not add stock.** These are alert thresholds, not quantities. Creating a product tells the system *"we sell this"*, and nothing more — the new product starts with zero stock and no batches, which is correct and is not an error.

Stock is added when it physically arrives, on the **Receive stock** screen (section 10.1), where the batch number and expiry date are read off the carton in front of you. That is also why this form asks for no batch number: a product is routinely catalogued before its first delivery arrives, when nobody has a carton to read.

When you save a **new** product, the system takes you straight to the Receive stock screen with that product already selected, because a product is nearly always created because stock of it is about to arrive. If it is not, just leave the screen — nothing is booked until you submit it.

**Regulatory information**

| Field | Meaning |
|---|---|
| Marketing authorisation no. | The registration number from the medicines regulator. |
| Barcode | For scanning. |
| Prescription required | Tick box. |
| Controlled substance | Tick box. Flags it for extra handling and record-keeping. |
| Notes | Free text. |

If the server rejects a field when you save, the error appears **next to that field**, not just as a banner at the top, and the page scrolls to it.

### 9.3 Medicine detail

**Address:** `/catalog/medicines/[id]`

Shows everything about one product, in cards: identification, pricing and VAT, inventory summary, current batches, and price history.

**This is where the catalogue and your stock meet.** The form that creates a product holds no quantities, but this page shows them: the inventory summary tells you how much you hold and how much is sellable, and the batches card lists every lot with its expiry and quantity. So when someone asks *"how much Furosemide do we have?"*, this page — or the Stock levels screen — answers it. The edit form never will.

If the batches card is empty, the product has been catalogued but no stock has been received against it yet. Go to **Receive stock** (section 10.1) to book some in.

**Buttons at the top:**
- **Edit.** Opens the edit form (needs edit permission). Changes the product's description and settings, never its stock.
- **Change price.** The correct way to reprice (needs the change prices permission).

**Why "Change price" is separate from "Edit"**

Editing a price through the ordinary form changes the number. Using **Change price** does the same, but requires you to enter a **reason**, and records the old price, the new price, who changed it, when, and why, in the price history.

A price change is a commercial decision that affects revenue and margin. When someone later asks why a product's price moved in March, the price history answers it. Use **Change price** for repricing.

**Price history** at the bottom shows every past change with dates, amounts, and reasons.

### 9.4 Categories

**Address:** `/catalog/categories`
**Permission needed:** view medicines to see, manage categories to change

Categories group products, for example Antibiotics, Analgesics, Antimalarials. Categories can have a parent category, so you can nest them.

This is reference data, so it is edited directly on the list in a pop-up dialog rather than on a separate page. **Create** opens an empty dialog; the pencil icon edits; the bin icon deletes.

| Field | Meaning |
|---|---|
| Code | Short identifier. |
| Name | Display name. |
| Parent category | Leave as "None (top-level category)" for a main category. |
| Description | Free text. |
| Status | Active or Inactive. |

### 9.5 Manufacturers

**Address:** `/catalog/manufacturers`
**Permission needed:** view medicines to see, manage manufacturers to change

The companies that make the products. Same pop-up dialog pattern as categories.

Fields: code, name, country, website, contact email, contact phone, status.

Note the difference from a supplier: a **manufacturer** makes the medicine; a **supplier** sells it to you. They are often different companies, so they are kept as separate lists.

### 9.6 Units of measure

**Address:** `/catalog/units`
**Permission needed:** view medicines to see, manage categories to change

How products are counted: each, box, carton, bottle. Same pop-up dialog pattern as categories and manufacturers.

Fields: code, name, base unit, units per pack, status.

**Units per pack** is what lets you buy in one unit and sell in another. If you buy cartons and sell boxes, set the carton's base unit to Box and its units per pack to how many boxes are actually in a carton. A delivery booked in cartons is then expressed correctly in the unit you stock.

**The conversion is always recorded, never guessed.** The system will not assume that a *boîte* means ten of something. Inferring conversions is how stock counts quietly drift away from reality, so if a relationship matters it has to be typed in.

**Names in both languages.** Type the name in whichever language you work in; the other is filled in for you and can be corrected by editing.

---

## 10. Inventory

Everything about physical stock.

### 10.1 Receive stock

**Address:** `/inventory/receive`
**Permission needed:** receive stock
**What it is for:** booking a delivery into stock. **This is the most important data entry screen in the system.** What you type here determines whether the business can trace a recall. Take your time.

This is the first item in the Inventory menu because it is the task you arrive with: a delivery is on the counter and it needs to go into stock. Everything else in that menu is something you consult afterwards.

**Two ways to receive**

At the top of the screen is a switch with two settings. Both look and work the same way — the only difference is whether a purchase order is there to fill the lines in for you.

| Setting | Use it when |
|---|---|
| **Against a purchase order** | The normal case. Goods arrived against an order somebody raised and approved. |
| **Direct delivery** | There is no purchase order: an emergency supply, a donation, or the stock you already held when the system was first set up. |

**Against a purchase order.** Choose the order from the list. It shows only orders that can still be received — approved, sent, or partially received. The lines fill in automatically, one per item still outstanding, with the quantity set to everything still owed, because a complete delivery is the normal case. Reduce the quantity for a partial delivery.

You can also reach this mode already filled in by pressing **Receive goods** on a purchase order, or on its row in the orders list.

**Direct delivery.** Start with a blank line and add as many as the delivery contains. If a product is not in the catalogue yet, you do not have to leave this screen — see *Creating a product without leaving the delivery* below.

**Where the goods are going**

| Field | Required | Meaning |
|---|---|---|
| Destination warehouse | Yes | Applies to every line on the delivery. |
| Delivery note | No | The supplier's delivery note number, usually beginning "BL-". |

Each line also has its own warehouse box, set to **Same as above**. Change it only when one item goes somewhere different — a cold-chain product heading for the refrigerated room while the rest of the delivery goes to the main store.

**Opening stock (initial inventory)**

In direct mode there is a tick box marked **Opening stock**. Tick it for stock you already held when the system was first set up.

It matters because that stock was counted off your own shelves, not bought from a supplier on this date. The system records it as an *opening balance* rather than a purchase, so your first month's purchase figures are not inflated by the entire starting inventory.

**Every line**

| Field | Required | Meaning |
|---|---|---|
| Product | Yes | Search by name or code. Fixed in purchase order mode — it is what was ordered. |
| **Batch number** | Yes | **Copy this exactly from the carton.** This is the traceability link. |
| **Expiry date** | Yes | From the carton. |
| **Quantity** | Yes | What actually arrived. |
| Unit cost | No | Pre-filled from the order, or from the catalogue's reference cost in direct mode. Change it if the supplier invoiced differently — what you leave here is what this batch is valued at. |
| Manufacturing date | No | If shown on the carton. |
| Warehouse | No | Leave as *Same as above* unless this line goes elsewhere. |

**Batch number and expiry date are never optional.** The system cannot read a carton and has no way to work them out on its own. If they were left blank, the stock would be untraceable in a recall — which is the one thing this screen exists to prevent. Never type a placeholder such as "N/A" or "TBD" into the batch number. If you genuinely do not have the carton in front of you, do not receive the stock yet.

**Creating a product without leaving the delivery**

In direct mode, if the product is not in the catalogue, open the product box and choose **＋ New medicine**. A small form opens on top of the screen asking only for what is essential: name, category, unit, dosage form, cost, and price. Category and unit can themselves be created from inside that form if they are missing.

Save it, and the new product appears already selected on the line you were filling in. Nothing you had typed is lost.

This exists because the old way — abandoning a half-entered delivery, going to the catalogue, creating the product, then starting the delivery again — was the most common way work got lost in this application.

**Checks that will stop you**

| Message | What it means |
|---|---|
| *Earlier than the minimum expiry required on this line* | The order specified a minimum acceptable expiry and this batch is short-dated. |
| *The expiry date cannot be in the past* | Expired stock cannot be received. Check you read the carton correctly. |
| *Two lines share the same product, batch and warehouse* | Combine them into one line with the total quantity. |
| *More than the amount outstanding on this line* | Either the supplier sent extra, which needs a conversation, or it is a typing mistake. |

Short-dated deliveries are caught here, on screen, before you submit, so you find out while the delivery is still on the loading dock and the driver is still there. Discovering it after the goods are booked in and the lorry has gone leaves you holding stock you cannot shift in time.

**"Before you can continue"** lists everything still blocking submission — a missing batch number, a missing expiry, a quantity of zero. With a dozen lines on a delivery, hunting for the one blank field behind a dead button is the slowest part of the job. The list tells you exactly where to look.

**What happens when you submit**

1. A batch is created for each line, with your batch number and expiry — or, if that batch already exists, its quantity is increased and the cost averaged across old and new units.
2. Stock is added and ledger lines are written.
3. On a purchase order receipt, freight, duty, and other charges are spread across the lines to give each batch its true landed cost.
4. On a purchase order receipt, the order moves to Partially received or Received.

**A direct delivery is booked all together or not at all.** If anything is wrong with line 9, nothing is saved — not even lines 1 to 8. You correct the problem and submit once. You will never be left holding a half-received delivery with no way to tell which lines went in.

**Getting the batch number right matters more than speed.** If it is mistyped, then when that batch is recalled the system will not find it, and the business will not be able to tell the regulator who received it. Everything downstream depends on this field.

### 10.2 Stock levels

**Address:** `/inventory/stock`
**Permission needed:** view stock
**What it is for:** the answer to "what do we have and what do we need to order?"

One row per product, summarising all its batches. This screen loads everything at once rather than in pages, because planning replenishment needs the whole picture.

| Column | Meaning |
|---|---|
| Product code | |
| Name | |
| Quantity remaining | Total physically held, all batches. |
| Quantity available | What you can actually sell. Remaining minus reserved. **This is the number that matters when a customer asks.** |
| Reorder level | The threshold set on the product. |
| Batches | How many separate batches make up the total. |
| Status | **Out of stock** (red), **Low stock** (orange), or **In stock** (green). |

**Filter:** All, Low stock, Out of stock, In stock.

**Why remaining and available differ.** If you hold 500 units and a colleague has a sale in progress that will take 100, remaining is 500 and available is 400. Selling on the available figure prevents two people promising the same stock.

### 10.3 Batches

**Address:** `/inventory/batches`
**Permission needed:** view batches
**What it is for:** the detailed view. Every individual batch you hold.

| Column | Meaning |
|---|---|
| Batch number | As printed by the manufacturer. |
| Name | Product name and code. |
| Warehouse | Where it is. |
| Expiry date | With a coloured badge for urgency. |
| Quantity remaining | Physically present. |
| Quantity available | Sellable. |
| Stock value | What this batch is worth. |
| Status | See below. |

**The expiry badge.** Colour alone is not enough for everyone to read, so urgent bands carry a warning triangle and words too.

| Condition | Badge |
|---|---|
| Already expired | Red, warning triangle, "Expired" |
| 30 days or fewer | Red, warning triangle, "Expiring soon" and the day count |
| 31 to 90 days | Orange, day count |
| More than 90 days | Grey, day count |

**Batch statuses**

| Status | Meaning | Sellable? |
|---|---|---|
| Active | Normal, good stock | Yes |
| Quarantined | Held, usually pending a quality check | No |
| Expired | Past its expiry date | No |
| Damaged | Physically unusable | No |
| Recalled | Withdrawn by manufacturer or regulator | No |
| Depleted | Fully used up | No, nothing left |
| Disposed | Destroyed and written off | No |

**Filters:** expiry horizon (30, 90, or 180 days) and status.

By default the list shows only batches with stock in them. If you filter by a status such as Expired, that restriction is lifted, otherwise the filter would hide the very rows you asked for.

**The adjust button.** If you have the adjust stock permission, each row has a slider icon that opens the stock adjustment dialog.

### 10.4 Stock adjustment dialog

Opened from the Batches screen. Used after a physical stock count when the shelf does not match the system.

| Field | Required | Meaning |
|---|---|---|
| Quantity remaining | Shown, not editable | What the system currently believes. |
| Counted quantity | Yes | What you actually counted. |
| Reason | **Yes** | Why there is a difference. |
| Notes | No | Extra detail. |

As soon as you enter a counted quantity, the difference appears: green with a plus for a surplus, red for a shortfall. If it matches exactly, it says "No discrepancies found", which is a valid result you can still submit.

**Why the reason is compulsory.** The Confirm button stays disabled until you type one. An unexplained inventory adjustment is exactly what an auditor investigates as possible theft. Making the reason feel obligatory rather than optional is the point.

Be specific. "Damaged in transit, 3 boxes crushed" is useful in six months' time. "Adjustment" is not.

The adjustment writes a permanent line in the stock ledger. It does not erase anything.

### 10.5 Stock movements

**Address:** `/inventory/movements`
**Permission needed:** view stock
**What it is for:** the complete ledger. Every stock change ever made.

| Column | Meaning |
|---|---|
| Movement date | Exact date and time. |
| Name | Product and code. |
| Batch number | Which batch. |
| Warehouse | Where. |
| Movement type | What kind of movement. |
| Change | The quantity, signed. Green with a plus for stock in, red for stock out. |
| Balance after | What the batch held immediately after this movement. |
| Stock value | Value of the movement. |
| Source document | Which invoice, order, or receipt caused it. |
| Performed by | Who did it. |

**Movement types**

| Type | Meaning | Direction |
|---|---|---|
| RECEIPT | Delivery booked in, with or without a purchase order | In |
| ISSUE | Sale to a customer | Out |
| SALE_RETURN | Customer returned goods | In |
| PURCHASE_RETURN | You sent goods back to a supplier | Out |
| TRANSFER_OUT | Left one warehouse | Out |
| TRANSFER_IN | Arrived at another warehouse | In |
| ADJUSTMENT_IN | Stocktake found more than expected | In |
| ADJUSTMENT_OUT | Stocktake found less than expected | Out |
| DAMAGE | Written off as damaged | Out |
| EXPIRY | Written off as expired | Out |
| DISPOSAL | Destroyed | Out |
| OPENING | Stock you already held when the system went live, entered with the *Opening stock* box ticked | In |

**Filters:** movement type and warehouse.

Nothing on this screen can be edited or deleted, by anyone, including administrators.

### 10.6 Warehouses

**Address:** `/inventory/warehouses`
**Permission needed:** view stock to see, manage warehouses to change (Store Manager and above)

Your physical storage locations, edited in a pop-up dialog.

| Field | Meaning |
|---|---|
| Warehouse code | Short identifier. |
| Name | Full name. A snowflake icon appears beside it if it is cold chain. |
| Address, City | Location. |
| Default warehouse | The one pre-selected on sales and goods receipts. Saves choosing every time on a single-site business. |
| Cold chain | Whether it has refrigeration. Determines where fridge-line products may be stored. |
| Status | Active or Inactive. |

### 10.7 Expiry report

**Address:** `/inventory/reports/expiry`
**Permission needed:** view stock
**What it is for:** deciding what to do about stock that is running out of time.

This is different from the Batches screen. It is ordered by urgency, and it totals the **value at risk**, meaning the money you stand to lose if this stock is not sold. That figure sits at the top right and is what tells you whether a clearance sale is worth running.

Choose a horizon: 30, 90, 180, or 365 days. 90 days is the default. You can also filter by warehouse.

Columns: expiry date with urgency badge, batch number, product, warehouse, quantity remaining, and value at risk.

The expiring-batches notification links straight to this screen with the right horizon already applied.

### 10.8 Reconciliation

**Address:** `/inventory/reports/reconciliation`
**Permission needed:** view stock
**What it is for:** checking that the system's stock figures agree with the permanent ledger.

The system holds a running total for each batch for speed, and a permanent ledger of every movement. These two must always agree. This screen re-adds the ledger and compares.

**A clean result is the expected result.** You get a green banner reading "No discrepancies found" and no table, because an empty table underneath a green banner just reads as a second, more confusing answer to the same question.

**If discrepancies are found**, a red banner appears with this text:

> *Batch balances disagree with the stock ledger. This is a data integrity incident rather than an operational warning: investigate before posting further movements.*

The table then shows each affected batch, the cached balance, the ledger balance, and the signed difference.

**What to do if this is not clean.** Do not carry on and hope it resolves itself. Report it to your manager and to whoever supports the system, immediately. A mismatch means the two records of your stock have drifted apart, and every figure derived from them is suspect until it is explained. This is the one screen in the system where a red result means stop.

The system also checks this automatically on a schedule and sends a critical notification if it finds anything.

---

## 11. Sales

### 11.1 New sale

**Address:** `/sales/new`
**Permission needed:** create sale
**What it is for:** the main selling screen.

The screen is in two columns on a computer: the sale on the left, the running total on the right. The total panel stays in view as you scroll.

#### Customer card

| Field | Required | Notes |
|---|---|---|
| **Customer** | Yes | Start typing to search by name or code. Shows the customer code and NIF. |
| **Sale type** | Yes | Cash sale or Credit sale. **Credit sale only appears if you have the sell on credit permission.** |
| **Warehouse** | Yes | Where the stock comes from. Pre-filled with the default warehouse. |

#### Line items card

One block per product. **Add line** adds another; the bin icon removes one (you cannot remove the last).

| Field | Notes |
|---|---|
| **Name** | Search and select the product. Only active products appear. |
| **Quantity** | How many units. |
| **Unit price (VAT incl.)** | Pre-filled from the catalogue. You can change it. Enter the price the customer pays, VAT included — the same basis as the catalogue. |
| **Discount %** | **Greyed out unless you have the apply discount permission.** If greyed, it says "You do not have permission to apply a discount." |
| **Line total** | Calculated live as you type. |

The VAT rate comes from the product itself, so a VAT-exempt product stays exempt and the preview total is right.

If you override the unit price, type the VAT-inclusive figure you quoted the customer. The system separates the tax from it exactly as it does for a catalogue price, so a line priced at 180 bills 180.

#### The totals panel

Updates as you type:

| Line | Meaning |
|---|---|
| Subtotal | All lines before discount and VAT. |
| Discount | Total discount, shown with a minus. |
| VAT | Total tax. |
| **Total** | The grand total. |

**On a credit sale**, a grey box also shows the customer's **available credit**.

#### Warnings you may see

**Licence expired (red).** *"This customer's licence has expired. The sale cannot proceed."* This customer's pharmacy licence has run out. You cannot sell to them until it is renewed and the record updated.

**Credit limit exceeded (orange).** The total exceeds their available credit. This appears before you submit, so you can act on it early rather than being refused at the end.

**"Before you can continue" (grey box).** Lists exactly what is still missing:
- Select a customer
- Select a warehouse
- Add at least one line with a product and a quantity

This exists because a dead button with no explanation is the most common way to get stuck. You should never have to guess.

#### Confirming the sale

**Confirm sale** does several things in one go, all or nothing:
1. Creates the sale record.
2. Picks the batches using FEFO.
3. Removes the stock from those batches and writes ledger lines.
4. Generates the invoice with its official number.
5. Takes you straight to the invoice, because your next step is to print it or take payment.

If any step fails, none of it happens. You never end up with stock gone but no invoice.

#### The credit limit override

If the sale exceeds the customer's credit limit, the server refuses it.

**If you do not have the override permission**, you see the refusal. Options: reduce the sale, take payment on an old invoice first, or ask someone with authority.

**If you do have the override permission** (a supervisor), a dialog opens asking for a **reason**, which is compulsory. The Confirm button stays disabled until you type one.

The dialog states plainly that the override is recorded against you in the audit log. It permanently records who authorised exceeding the limit, when, and why. Write a real reason.

### 11.2 Sales list

**Address:** `/sales`
**Permission needed:** view sales

All sales. Columns: sale number, customer name and code, date, type (Credit sales are badged orange), invoice number, total, status.

**Filter:** All, Cash sale, Credit sale.

**New sale** at the top right if you have permission.

Click any row to open the sale.

### 11.3 Sale detail

**Address:** `/sales/<sale number>`
**Permission needed:** view sales

Everything about one sale.

**The lines**, each showing quantity, unit price, discount, and line total. Under each line the system shows **which batches were actually issued** against it — this is the traceability record, and it is the screen you come to when a recall means you need to know who received a particular batch.

**The totals** — subtotal, discount, VAT, and grand total — and, if you have the margin permission, the profit on the sale.

**The sale card** — customer, sale type (cash or credit), salesperson, date, and status. If the sale produced an invoice, the invoice number is a link straight to it.

**Notes** appear at the bottom, with a Translate control if a colleague wrote them in the other language (see section 6).

**What you can do here depends on the status.**

A **draft** sale carries a banner: *"This draft is not confirmed: no stock has been issued and no document raised. Reopen it to finish and confirm the sale."* This is the important thing to understand about drafts — a draft has reserved nothing, issued nothing, and produced no receipt or invoice. It is a basket, not a sale. You can **edit** it, or **delete** it outright, because deleting something that never affected stock or money leaves nothing behind to explain.

A **confirmed** sale can be **cancelled** if you have the permission, but never edited and never deleted. By then stock has moved and a document exists, so the correction has to be a new record that reverses it rather than a quiet edit. See section 3 on why mistakes are corrected by adding.

### 11.4 Returns

**Address:** `/sales/returns`
**Permission needed:** view sales

A read-only register of returns. A blue note at the top explains why: *"A return is raised from the sale it reverses. This list is read-only."*

Returns start from the original sale, not from here, because a return must be tied to what was actually sold. Rows are not clickable; the originating sale number is shown on the row.

Columns: return number, return date, sale number, customer, reason, credit note number, refund amount.

**How a return works.** Processing a return requires the process return permission (Pharmacist and above). You choose which lines and quantities are coming back, and give a reason.

The key question is **"Return to sellable stock?"**. The system warns: *"Tick only if storage conditions were demonstrably maintained."*

This is a pharmaceutical judgement, not an administrative one. If a customer kept a refrigerated product in a hot car, it is no longer safe to sell even though it looks fine. If you tick the box, the stock goes back into sellable inventory and can be sold to someone else. If you do not, it comes back as unsellable.

The return generates a **credit note**, which reduces what the customer owes.

---

## 12. Invoicing

### 12.1 Invoices list

**Address:** `/invoicing/invoices`
**Permission needed:** view invoices

| Column | Meaning |
|---|---|
| Invoice number | The official number, for example FAC-2026-000148. |
| Customer | Name and NIF. |
| Invoice date | |
| Due date | With a red badge showing days overdue if applicable. |
| Total amount | |
| Balance due | Red and bold if overdue. |
| Status | Draft, Posted, Partially paid, Paid, Overdue, Cancelled. |

**Filter:** All, Outstanding invoices, Overdue, Draft.

The download icon on each row saves the invoice PDF, in your current language. Requires the print invoice permission.

### 12.2 Invoice detail

**Address:** `/invoicing/invoices/[id]`

The full invoice.

**Four summary cards:** Invoice date, Due date, Total amount, and Balance due. The balance is red with the word "Outstanding" if money is owed, green with "Paid" if settled. The word is there because red versus green alone is not readable by everyone.

**Buttons** (each depends on a permission):
- **Download PDF.** Saves the invoice.
- **Send by email.** Emails it to the customer, in their preferred language.
- **Record payment.** Only appears when there is a balance to pay.

**The posted warning.** If the invoice is posted, an orange banner reads:

> *A posted invoice can no longer be modified. Issue a credit note to correct it.*

This is not a limitation of the software. A posted invoice is a legal tax document that has been given a number in a gap-free sequence. Editing or deleting one would break the sequence and look, to the tax authority, like a concealed sale. The credit note exists precisely to correct posted invoices in a way that leaves a visible trail.

**Line items table.** Each line shows number, description, quantity, unit price, discount, VAT rate (or "VAT exempt"), and line total.

Beneath each description are the **batch numbers and expiry dates** supplied on that line. This is not clutter. It is the customer's own traceability record. If a batch is recalled, they can check their invoices and find out immediately whether they received any of it.

Totals follow: subtotal, discount if any, VAT, total, and amount paid if any.

**Print count.** At the bottom, if the invoice has been printed, you see how many times. Every print is recorded, and copies after the first are stamped as duplicates. This is a standard control so that one invoice cannot be printed several times and used as if it were several sales.

**Recording a payment.** The dialog asks for:

| Field | Notes |
|---|---|
| Total amount (BIF) | Pre-filled with the full balance. Change it for a part payment. |
| Payment method | Cash, Bank transfer, Cheque, Mobile money, Card, Other. |
| Bank reference | The transfer or cheque reference. |
| Notes | Free text. |

The payment is applied to this invoice, and the balance updates immediately.

### 12.3 Payments

**Address:** `/invoicing/payments`
**Permission needed:** view invoices to see, record payment to record

All money received.

| Column | Meaning |
|---|---|
| Payment reference | With a red "Reversed" badge if it has been reversed. |
| Payment date | |
| Customer | Name and code. |
| Payment method | |
| Amount received | Total money received. |
| Allocated | How much has been matched to invoices. |
| Unallocated | Money received but not yet applied. **Shown in bold orange when above zero.** |

**Why unallocated money is highlighted.** It means you have the customer's money but the system has not decided which invoice it settles. It is exactly what someone reconciling the accounts is looking for, so it stands out.

**Recording a payment from this screen.** The dialog asks for customer, amount, method, bank reference, and notes. It does not ask which invoices to settle.

**How the money is applied:** oldest invoice first. This is the standard finance convention, and doing it automatically means a customer's oldest debt is always cleared first rather than depending on who processed the payment.

To apply a payment to one specific invoice instead, record it from that invoice's own page.

**Reversing a payment.** The undo arrow reverses a payment, for example when a cheque bounces. A **reason is compulsory**. The payment is not deleted; it is marked Reversed and the reversal is recorded. The customer's balance goes back up.

**Payment receipts are issued for you.** The moment an invoice's balance reaches zero, the system issues a numbered **payment receipt** acknowledging that it has been settled, and it is available from the invoice. Nobody has to remember to produce one — a customer who has just paid expects a document confirming it, and the receipts that depend on someone remembering are the ones that never get issued.

One receipt per invoice. If a payment is later reversed and the invoice reopens, the receipt is **cancelled rather than deleted**, so the numbered series stays unbroken and the document you handed the customer remains on file.

The receipt carries no figures of its own: every amount on it is read from the invoice it acknowledges. That way a credit note issued afterwards cannot leave a receipt quoting a total that is no longer true.

### 12.4 Declaring invoices to the OBR

**Permission needed:** declare invoice to OBR (store managers and administrators)

Burundi requires sales documents to be declared electronically to the OBR. When this is switched on, the system handles it for you. Most days you will never think about it — this section is for the days you do.

**What happens when you post an invoice.** The invoice is given a **fiscal signature**: a code built from the pharmacy's NIF, the identifier the OBR issued when this software was approved, the invoice date, and the invoice number. It looks like this:

> `4001902867/SYS-0042/2026-08-01/FAC-2026-000148`

The signature is worked out on this computer. It does **not** need the internet. This matters: a customer at the counter is never kept waiting because the connection to Bujumbura is slow or down.

**When the declaration is actually sent.** Every ten minutes the system sends any invoices that are waiting. If the connection is down, they simply wait and go out when it returns. Nothing is lost and nobody has to remember to do anything.

**The fiscal status.** Separate from the ordinary invoice status, because they answer different questions. "Paid" tells you about the money. The fiscal status tells you about the tax authority.

| Fiscal status | What it means | Do you need to act? |
|---|---|---|
| **Not declarable** | This document is not declared. Proformas, and anything dated before the system went live. | No |
| **Awaiting declaration** | Posted and queued. It will be sent within a few minutes. | No |
| **Declared to OBR** | Accepted. The OBR registration number is on the invoice. | No |
| **Rejected by OBR** | The OBR refused it, and the system has stopped retrying. | **Yes** |
| **Cancellation declared** | The invoice was cancelled and the OBR has been told. | No |

**Why "Awaiting declaration" is normal.** An invoice sitting in this state for a few minutes is the system working correctly. It only deserves attention if it stays there for hours, which means the connection has been down that long.

**What "Rejected" means and what to do.** The system tries several times, waiting longer between each attempt. If it still cannot get the invoice accepted, it stops and marks it Rejected rather than retrying forever. You will get a notification.

Rejection almost always means something on the invoice does not match what the OBR expects — commonly a customer NIF that is wrong or missing. To deal with it:

1. Open the invoice. The reason the OBR gave is shown on screen.
2. Fix the underlying cause. If it is the customer's NIF, correct it on the customer record.
3. Click **Declare to OBR** on the invoice to try again immediately.

You do not have to wait for the next automatic attempt, and you do not need to cancel and reissue the invoice.

**A rejected invoice is still a valid invoice.** It is posted, it is numbered, the customer owes the money. What is missing is the declaration. Do not cancel it and start again — that would break the numbering sequence for no reason.

**On the printed invoice.** Once the OBR has accepted a document, its PDF carries the fiscal signature, the OBR registration number, and the OBR's electronic signature in a box near the bottom. Before acceptance these are not printed, because printing a registration number for a filing that has not happened yet would be misleading.

**Cancelling a declared invoice.** If you cancel an invoice the OBR has already accepted, the system tells the OBR to withdraw it. This happens automatically in the background, the same way declarations do. If the invoice had not yet been sent, it is simply removed from the queue — there is nothing to withdraw.

---

## 13. Purchasing

Buying stock from suppliers, with approval controls.

### The purchase order lifecycle

```
DRAFT ──submit──> PENDING APPROVAL ──approve──> APPROVED ──mark sent──> SENT
                         │                                                │
                         └──reject──> REJECTED                            │
                                        │                                 │
                                        └──resubmit──> PENDING APPROVAL   │
                                                                          ▼
                                                       PARTIALLY RECEIVED / RECEIVED
```

At any point before it is received, an order can be CANCELLED (with a reason).

### 13.1 Purchase orders list

**Address:** `/purchasing/orders`
**Permission needed:** view purchase orders

| Column | Meaning |
|---|---|
| Order number | |
| Supplier | |
| Order date | |
| Expected delivery | With a red "Overdue" badge if the date has passed and goods have not arrived. |
| Total | |
| Quantity received | Progress as a percentage. |
| Status | |

**Filter:** All, Pending approval, Approved, Partially received, Received.

If you have the receive goods permission and the order is in a receivable state (Approved, Sent, or Partially received), a **Receive goods** button appears directly on the row. It opens the Receive stock screen (section 10.1) with this order already selected and its lines filled in.

If you have permission to raise orders, a **New purchase order** button sits at the top right.

### 13.2 New purchase order / Edit purchase order

**Address:** `/purchasing/orders/new` and `/purchasing/orders/[id]/edit`
**Permission needed:** raise purchase orders (Pharmacist and above)
**What it is for:** writing the order that authorises a supplier to ship goods to you.

The same form is used for creating and editing, so the two can never disagree.

**Supplier card**

| Field | Required | Meaning |
|---|---|---|
| Supplier | Yes | Only **approved** suppliers can be ordered from — see below. |
| Delivery warehouse | Yes | Where the goods should arrive. |
| Expected delivery | No | Left blank, the system works it out from the supplier's usual lead time. |
| Supplier reference | No | Their quotation or reference number, if you have one. |

**Only approved suppliers.** If the supplier you need is not in the list, or the order is refused with *"This supplier is not approved"*, someone with supplier permissions has to approve them first on the Suppliers screen. Buying medicines from an unvetted supplier is a regulatory finding, not merely a risk, so the system blocks it when the order is raised rather than discovering it when the goods turn up.

**Order lines**

One block per product, with **Add a line** underneath.

| Field | Required | Meaning |
|---|---|---|
| Medicine | Yes | Search by name or code. If it is not in the catalogue, choose **＋ New medicine** to create it here without losing the order. |
| Quantity ordered | Yes | |
| Supplier unit cost | Yes | What this supplier is charging you per unit on this order. |
| Discount % | No | Any agreed discount on this line. |
| Minimum acceptable expiry | No | See below. |

**Supplier unit cost is pre-filled, and you are expected to change it.** When you pick a product, the system fills this in from the catalogue's reference cost and shows that reference underneath the field. It is a starting point, not the price. Type what the supplier actually quoted — that figure is what the order is placed at, what the supplier is paid, and what the delivered batch is valued at. The catalogue reference is left alone.

**A line cannot be ordered at zero.** If the cost is blank or zero, the order will not save and the reason appears at the bottom of the form. A zero-cost line would produce goods that appear to have cost nothing, which understates what your stock is worth for as long as that batch is held.

**Minimum acceptable expiry is worth setting.** It is the earliest expiry date you are willing to accept for this product. When the delivery arrives, anything shorter-dated is **refused at receipt**, on screen, while the driver is still there. Without it, short-dated stock is accepted quietly and becomes a write-off later.

Each block shows its own line total as you type.

**Landed costs card**

| Field | Meaning |
|---|---|
| Freight | Transport cost for the whole delivery. |
| Customs duty | Import duty. |
| Other charges | Clearing fees and anything else. |

These are entered once for the order, not per line. When the goods arrive, the system spreads them across the lines in proportion to value, so each batch carries its true landed cost. For a Burundian wholesaler these charges commonly add 15–30% to what the goods really cost, which is the difference between a sale that looks profitable and one that is.

Underneath, a running **subtotal** and **total** update as you type.

**Two save buttons**

| Button | What it does |
|---|---|
| **Save draft** | Saves the order as a draft. Nothing is sent and nobody is asked to approve it. Come back and finish it later. |
| **Save and submit** | Saves it and sends it for approval in one action. |

Use **Save and submit** when the order is finished. A draft that is never submitted is invisible to whoever has to approve it, and orders have been forgotten that way.

**Editing an order**

The **Edit** button appears on an order's detail screen only while it is a **Draft** or has been **Rejected**.

Once an order is approved it can no longer be edited, and the Edit button disappears. This is deliberate: an approval is somebody putting their name to a specific set of quantities and prices. If those could be changed afterwards, the approval would mean nothing. If an approved order is wrong, cancel it and raise a new one.

A **rejected** order, on the other hand, is editable precisely so you can fix what the approver objected to and submit it again.

When you edit, the lines you save **replace** the previous ones entirely. The totals are recalculated.

### 13.3 Purchase order detail

**Address:** `/purchasing/orders/[id]`

The full order, and the place where approval happens. Approval-request notifications lead here, so that an approver can read the order and act on it without hunting for a second screen.

**Buttons, depending on status and your permissions:**

| Button | Appears when | Permission |
|---|---|---|
| Edit | Status is Draft or Rejected | raise orders |
| Submit for approval | Status is Draft or Rejected | submit order |
| Approve | Status is Pending approval | approve order |
| Reject | Status is Pending approval | approve order |
| Mark as sent | Status is Approved | approve order |
| Receive goods | Order can be received | receive goods |
| Cancel | Not yet received, cancelled, or closed | cancel order |

**Receive goods** opens the Receive stock screen (section 10.1) with this order selected and its outstanding lines already filled in.

**Separation of duties.** If you raised the order yourself, the Approve button is disabled and a message appears:

> *You cannot approve a purchase order that you raised yourself.*

This is a deliberate financial control. One person alone must never be able to both commit the company to a purchase and authorise it, because that is the simplest route to fraud. Someone else with approval authority must approve it. The rule is enforced on the server; showing it here just saves you clicking a button that would be refused.

**Rejection.** Rejecting requires a **reason**, which is then shown in a red banner at the top of the order so the person who raised it knows what to fix. They can correct it and resubmit.

**General information card:** supplier, warehouse, order date, expected and actual delivery, supplier invoice number, requested by, and approved by with the date.

**Totals card:**

| Line | Meaning |
|---|---|
| Subtotal | Cost of the goods. |
| Discount | |
| VAT | |
| Freight | Transport cost. |
| Customs duty | Import duty. |
| Other charges | Anything else. |
| **Landed cost** | The true total cost of getting the goods onto your shelf. |
| Total | |

**Why landed cost is separated.** Landed cost is what reaches the batch cost, and therefore what your stock is really valued at and what your margin is really calculated from. A product bought at 1,000 BIF with 200 BIF of freight and duty per unit costs you 1,200. Selling at 1,100 looks like profit against the purchase price and is actually a loss. Landed cost is why the system knows the difference. The freight and duty are spread across the delivery in proportion to value.

**Line items table:** product, quantity ordered, quantity received, quantity outstanding, supplier unit cost, and total. Outstanding is shown in bold when there is still something to come.

### 13.4 Receiving goods against an order

Receiving now happens on its own screen — **Receive stock**, the first item in the Inventory menu, documented in full at section 10.1.

Press **Receive goods** on an order (or on its row in the orders list) and that screen opens with the order already selected and its outstanding lines filled in.

It moved because a delivery does not always arrive against a purchase order. Emergency supplies, donations, and the stock you already held when the system was set up all need booking in too, and none of them has an order to hang off. One screen now handles both, so there is only one place to learn and one place to look.

### 13.5 Goods receipts

**Address:** `/purchasing/receipts`
**Permission needed:** view purchase orders

A read-only register of deliveries received **against a purchase order**. A note explains: *"This register lists receipts booked against a purchase order and cannot be edited. To book in a delivery, go to Inventory → Receive stock."*

Columns: receipt number, receipt date, order number, supplier, quantity received, quantity rejected.

**Direct deliveries do not appear here**, because they have no purchase order to belong to. To see every arrival, whatever its route, use **Stock movements** (section 10.5) and filter on the Goods receipt and Opening balance movement types.

### 13.6 Supplier invoices

**Address:** `/purchasing/supplier-invoices`
**Permission needed:** view supplier invoices

The bills your suppliers have sent you: what the business owes, and for what. An order is what you asked for; a supplier invoice is what you are being charged.

**Why it is a separate document from the order.** The two do not match one-to-one in real life. A supplier commonly bills several orders on one invoice, bills part of an order after a partial shipment, or bills for something that never had an order at all — freight, customs clearance, a storage charge. So an invoice may be linked to a purchase order, but it does not have to be.

**The amounts are the supplier's, not yours.** The system stores what the supplier actually charged rather than recalculating it from the order. If their invoice disagrees with your order, that disagreement is a fact you need to see and settle with them — not something the system should quietly paper over.

Columns: internal reference, supplier invoice number, supplier, invoice date, due date, total, balance due, status.

**Two numbers per invoice.** The **supplier invoice number** is the number *they* put on their document — you copy it in as printed. The **internal reference** is your own sequential number for the payable. Two different suppliers both numbering their invoices "001" is perfectly ordinary; the system only refuses the same number twice *from the same supplier*.

**Recording an invoice** needs the record supplier invoice permission. You enter the supplier, their invoice number, the dates, and the amounts — subtotal, VAT, and separately freight, customs duty, and other charges. Freight and duty appear here as well as on the order because the order's figures are the estimate and these are what you were actually billed.

If the supplier invoiced in a foreign currency, enter the currency and the exchange rate applied.

**Statuses**

| Status | What it means |
|---|---|
| Draft | Being entered; not yet a live payable. |
| Awaiting payment | Recorded and owed; nothing paid yet. |
| Partially paid | Some money has gone against it. |
| Paid | Settled in full. |
| Overdue | Past its due date with a balance still owing. |
| Cancelled | Withdrawn. |

The **balance due** is worked out from the payments allocated to the invoice, and recalculated from scratch every time — never nudged up and down. A running tally quietly accumulates errors from every reversal, and a wrong payable balance stays invisible until a supplier disputes their statement.

### 13.7 Supplier payments

**Address:** `/purchasing/supplier-payments`
**Permission needed:** view supplier payments

Money paid out to suppliers, and which of their invoices it settled.

**Recording a payment** needs the record supplier payment permission — deliberately *not* the same permission as recording an invoice. The person who enters what is owed should not also be the one who pays it.

You record the payment against the **supplier**, not against a single invoice, then decide how it is split. Enter the supplier, the amount, the date money actually left, the method (cash, bank transfer, cheque, mobile money, card, other), and a payment reference — the cheque number, transfer reference, or mobile money code.

**How the money is allocated.** Two modes:

- **Automatic** — the system settles the oldest due invoices first until the money runs out. This is what most payments are, and the screen shows you which invoices it is about to clear before you confirm.
- **Manual** — you type the amount against each invoice yourself. Use it when a payment deliberately part-pays one bill and clears another, which no automatic rule can work out for you. Switching to manual starts from whatever the automatic plan would have done, so you adjust rather than start from nothing.

Anything you do not allocate stays on the payment as **credit on account** with that supplier, available against their future invoices.

**Reversing a payment** needs the reverse supplier payment permission, and a **reason is compulsory**. A payment is never deleted: it is marked reversed, the invoices it was allocated to reopen, and their balances are recalculated. Money that moved and came back is two events in the cash record, and erasing the first would make the bank statement impossible to reconcile against your books. Reversed payments are excluded from the cash outflow report, because the money did come back.

---

## 14. Partners

Customers and suppliers.

### 14.1 Customers list

**Address:** `/partners/customers`
**Permission needed:** view customers

Columns include customer code, business name, type, NIF, credit limit, outstanding balance, and status. Search by name, code, or NIF.

### 14.2 New customer / Edit customer

**Address:** `/partners/customers/new` and `/partners/customers/[id]/edit`
**Permission needed:** create or edit customers

**Identification**

| Field | Required | Meaning |
|---|---|---|
| Business name | Yes | Legal name. Minimum 2 characters. |
| Trading name | No | The name they trade under, if different. |
| Customer type | Yes | Pharmacy, Hospital, Clinic, NGO, and so on. Defaults to Pharmacy. |
| **NIF** | Conditional | Tax number. See the rule below. |
| Trade register no. | No | Commercial registration number. |
| Licence number | No | Their pharmacy licence. |
| Licence expiry | No | When it expires. |

**The NIF rule.** A NIF is optional for a cash customer and **compulsory for a customer on credit terms**. If you choose any NET payment term without a NIF, you get:

> *A NIF is required for a customer with credit payment terms.*

The reason: a credit sale produces a formal invoice that is a tax document, and a tax document must identify the taxpayer receiving it. A customer who cannot be identified to the OBR cannot be invoiced on credit.

The expected NIF format is 4 digits followed by 4 to 8 letters or numbers. If it does not match, you get: *"Invalid NIF format. Expected 4 digits followed by 4 to 8 alphanumeric characters."*

The check is deliberately a little loose, because OBR formats have varied over the years, and wrongly rejecting a genuine taxpayer at the counter is worse than accepting an odd-looking one that the server will check again anyway.

**Contact details:** contact person, email, telephone, address, city (defaults to Bujumbura), province.

Telephone must be a Burundi number: 8 digits, optionally prefixed with +257.

**Commercial terms**

| Field | Meaning |
|---|---|
| Payment terms | Cash, NET 7, NET 15, NET 30, NET 45, NET 60, NET 90. |
| Credit limit | **Only editable when creating.** See below. |
| Discount percent | A standing discount for this customer. |
| Notes | Free text. |

**Why the credit limit can only be set at creation.** When editing an existing customer, the field disappears, with the explanation:

> *The credit limit is changed from the customer record, which requires a reason and keeps the history.*

Changing a credit limit is a credit decision. Done through the ordinary edit form it would leave no trace of who decided, when, or why. It therefore has its own action on the customer's detail page, which requires the set credit limit permission and a written reason. A credit decision with no trail is exactly what that separate route exists to prevent.

### 14.3 Customer detail

**Address:** `/partners/customers/[id]`

Everything about one customer, including their credit position and their statement.

**Credit information shown:**

| Figure | Meaning |
|---|---|
| Credit limit | The maximum they may owe. |
| Outstanding balance | What they owe now. |
| Available credit | Limit minus outstanding. **This is what a new sale is checked against.** |
| Credit utilisation | The percentage of the limit in use. |

**Actions (each needs a permission and a reason):**

**Change credit limit.** Set a new limit. Reason compulsory. Recorded permanently.

**Block credit.** Stops all credit sales to this customer immediately. Reason compulsory. Use when payments stop or the relationship is in dispute.

**Unblock credit.** Restores credit. A reason is not required by the server here, and the form does not demand one.

**Account statement.** The full transaction history: invoices, payments, credit notes, running balance.

**The licence warning.** If their pharmacy licence has expired, it is flagged here and on the New sale screen, where it blocks the sale outright.

### 14.4 Suppliers

**Address:** `/partners/suppliers`
**Permission needed:** view suppliers

Companies you buy from. The form covers identification, contact details, banking details (bank name, account, SWIFT/BIC), invoicing currency, and average lead time in days.

**Supplier approval.** A supplier must be approved before it can be used on a purchase order:

> *Only approved suppliers may be selected on a purchase order.*

This stops stock being bought from a source nobody has vetted, which for medicines is a patient safety issue as much as a commercial one.

**Supplier detail** additionally shows performance figures: total orders, orders received, on-time deliveries, late deliveries, average delay in days, on-time rate, and total purchase value. This is the evidence for a conversation about a supplier who keeps arriving late.

---

## 15. Accounting

Where the money goes. This module answers the day-to-day question the rest of the system could not: the sales screens tell you what came in and the purchasing screens tell you what you bought, but rent, salaries, electricity, and fuel were nowhere.

**What this is not.** It is not a general ledger. There is no chart of accounts, no double-entry, no trial balance. A wholesaler who needs those buys accounting software and files through an accountant. What that arrangement leaves out is the running view of where the money is going, and that is exactly what this module gives you, built from records the pharmacy already keeps.

**Money leaves the business in two shapes**, and the module keeps them apart:

| | **Expense** | **Supplier payment** |
|---|---|---|
| What it is | An operating cost: rent, salaries, utilities, shipping | Settling a supplier's invoice for goods |
| Where you record it | Accounting → Expenses | Purchasing → Supplier payments (section 13.7) |

Supplier payments are *read* by the accounting reports, never re-entered here. There is one row for each payment, in one place, which is what stops the same money being counted twice in a cash outflow report.

### 15.1 Financial overview

**Address:** `/accounting/overview`
**Permission needed:** view financial overview (store managers and administrators)

The money-in, money-out picture for a period you choose.

**Money in.** Gross revenue, VAT, net revenue, cost of goods, and gross profit — taken from confirmed sales, so these figures match what the sales reports say rather than being worked out a second way. Draft sales (quotations) and cancelled sales are excluded.

**VAT is not revenue.** It is deducted to reach net revenue, because it is money you are holding on behalf of the OBR, not money you earned.

**Money out.** Operating expenses, supplier payments, total cash outflow, and — separately — unpaid expenses.

**The result.** Gross profit minus operating expenses gives the **operating result**, with a margin percentage.

> **Read the operating result as an indicator, not a statutory profit.** It is deliberately labelled that way. It does not carry depreciation, tax, or anything else your accountant will bring to a formal set of accounts.

**Position.** Outstanding payables — what you still owe suppliers in total, and how many suppliers that is spread across.

### 15.2 Expenses

**Address:** `/accounting/expenses`
**Permission needed:** view expenses

Every operating cost recorded, newest first. Columns: reference, date, category, description, payee, amount, status.

**Recording an expense** needs the record expense permission. Pharmacists have it as well as managers: the small costs that come up at the counter should be booked by the person who incurred them.

**Editing one afterwards is a separate permission**, held by managers and administrators. So a pharmacist can enter a cost but not go back and change the figure — if they have made a mistake, a manager corrects it. Approving, marking paid, and cancelling all sit with management too.

**Two dates, and they are not the same thing.** The **expense date** is when the cost was *incurred*; the **date paid** is when the money actually left. An electricity bill entered on the 1st and paid on the 20th is a commitment for the whole period, and treating it as spent on the 1st would misstate that month's actual outflow. The cash outflow report uses the second date; the expenses-by-category report uses the first.

**Amounts are entered VAT-inclusive**, which is how a receipt from a landlord or a utility actually reads. Record the **VAT included** separately only when you know it and it is recoverable — the system will not assume a VAT figure, because guessing tax on a cost that never carried any overstates what you can reclaim.

**Who was paid.** The **paid to** field is free text, because most overheads go to people who are not pharmaceutical suppliers — a landlord, the water company, a mechanic. Forcing them into the supplier list would clutter the list you pick from when raising purchase orders. When a cost *does* relate to a supplier, or to a specific consignment, you can link the **supplier** and the **purchase order** as well — which is how freight and clearing charges stay traceable to the delivery they were incurred for.

**Notes.** Separate from the description, and deliberately roomy. The description is a one-line summary for the list; the notes are where an unusual cost gets a proper explanation — what it covered, why it was needed, who authorised it.

**Statuses**

| Status | What it means | Editable? |
|---|---|---|
| Draft | Being entered. | Yes |
| Recorded | Booked as a cost incurred. | Yes |
| Approved | Signed off for payment. | No |
| Paid | Money has left. | No |
| Cancelled | Withdrawn, with a reason. | No |

Once an expense is approved it is history, and history is not edited. Approving needs the approve expense permission (managers and administrators) — the same separation as elsewhere: the person who records a cost is not the person who signs it off.

### 15.3 Expense categories

**Address:** `/accounting/categories`
**Permission needed:** manage expense categories

The headings expenses are grouped under in reports. Fourteen are set up ready to use: rent, salaries, utilities, shipping and freight, office supplies, marketing, maintenance, fuel and travel, telephone and internet, insurance, taxes and licences, professional fees, bank charges, and other.

You can add your own and rename any of them. Each category has a stable internal **code** that the reports group by, so renaming a category for display never breaks the history behind it.

**Names in both languages.** Type the name in whichever language you work in and the system fills in the other for you, machine-translated. It is a starting point — correct it by editing if the wording matters. This is the same behaviour as catalogue categories.

A category that has expenses against it cannot be deleted; deactivate it instead so it stops appearing on new expenses while its history stays intact.

### 15.4 Accounting reports

**Address:** `/accounting/reports`
**Permission needed:** view accounting reports (store managers and administrators)

Four reports, chosen from the buttons at the top, each over a date range you set.

| Report | What it tells you |
|---|---|
| **Expenses by category** | Where the operating money went, by heading, for the period. Grouped on the expense date — when the cost was incurred. |
| **Supplier payments** | What was paid out to suppliers, and to whom. Filterable by supplier. |
| **Outstanding balances** | What you still owe each supplier, as at a date you choose. This one takes a single date, not a range. |
| **Cash outflow** | Everything that actually left the bank in the period, from both sources side by side: supplier payments and paid expenses, then the total. |

**Cash outflow counts money that moved.** Both halves filter on when payment happened, not when the obligation arose. Reversed supplier payments are left out, because that money came back. Costs incurred in the period but not yet settled are reported separately as unpaid expenses, so you can see what is still to come.

---

## 16. Reports

**Address:** `/reports`
**Permission needed:** at least one report permission. You only see the reports you are allowed.

### How the screen works

A **Filters** card at the top, then one card per report. Each report card lists which filters it actually uses, because a filter that is set but quietly ignored is worse than no filter at all. If a card does not list "Warehouse", setting a warehouse will not affect it.

**Available filters:** date from, date to, warehouse, category, product, customer, salesperson.

Each report card has three buttons: **CSV**, **Excel**, and **PDF**. The file downloads to your computer.

### The eight reports

| Report | What it tells you | Filters it uses | Permission |
|---|---|---|---|
| **Inventory valuation** | What all your stock is worth. | Warehouse, Category | Inventory reports |
| **Expiry report** | Batches approaching expiry and the value at risk. | Warehouse | Inventory reports |
| **Stock movements** | Every stock change in a period. | Date range, Warehouse, Product | Inventory reports |
| **Dead stock** | Stock that has not moved. Money sitting still. | Warehouse | Inventory reports |
| **Sales report** | Sales analysis. | Date range, Customer, Salesperson | Sales reports |
| **Receivables ageing** | Who owes you what, grouped by how late it is. | None | Financial reports |
| **Gross trading result** | Revenue minus cost of goods sold. | Date range | Financial reports |
| **Compliance report** | Regulatory and audit information. | Date range | Compliance reports |

**A caution on the gross trading result.** This is not your profit. It is revenue minus the cost of the goods only — rent, salaries, electricity, and transport are not deducted from it.

Those costs *are* now recorded, in the Accounting module (section 15). The figure that takes them off is the **operating result** on the Financial overview. Use the gross trading result to judge how well you are buying and selling; use the operating result to judge whether the business is covering its costs.

**Receivables ageing explained.** It groups what customers owe by how overdue it is: not yet due, 1 to 30 days, 31 to 60, 61 to 90, over 90. Older money is harder to collect, so this report is the basis of a collections plan.

If you have no report permissions at all, the page says *"You do not have permission to perform this action."*

---

## 17. Administration

Restricted to System Administrators (and the Audit log, also to Auditors).

### 17.1 Users

**Address:** `/admin/users`
**Permission needed:** view users

| Column | Meaning |
|---|---|
| Full name | With their email underneath. |
| Employee number | |
| Job title | |
| Roles | One badge per role. |
| Last sign in | |
| Status | **Suspended** (red), Active (green), or Inactive (grey). |

Suspension is shown ahead of inactivity, because a suspended account is a deliberate act an administrator needs to notice at a glance.

**Filter:** All, Active, Inactive.

### 17.2 New user / User detail

Creating a user asks for first name, last name, email, employee number, job title, language, and roles.

**Password field:** *"Leave blank to generate a temporary password automatically."*

When a temporary password is generated it is shown **once**, with:

> *Pass this password to the user. They must change it at first sign in.*

Copy it before closing the dialog. It cannot be retrieved afterwards.

**Actions on the user detail page:**

| Action | Effect |
|---|---|
| **Assign roles** | Change which roles the user holds. Takes effect at once. |
| **Reset password** | Issues a new temporary password. |
| **Suspend** | Blocks sign-in immediately. Reversible. |
| **Reactivate** | Restores a suspended account. |
| **Revoke sessions** | Signs the user out of every device at once. |

**Active sessions** lists where the user is currently signed in: device, IP address, and when they were last seen. If someone's account may be compromised, revoke the sessions and reset the password.

Changing a password automatically ends every session that user has, everywhere.

### 17.3 Roles

**Address:** `/admin/roles`
**Permission needed:** view users to see, manage roles to change

| Column | Meaning |
|---|---|
| Role code | Internal identifier. |
| Name | With a "System role" badge where applicable. |
| Permissions | How many. |
| Users | How many people hold it. |
| Status | |

**System roles.** The six standard roles ship with the application:

> *System roles ship with the application and cannot be modified.*

They cannot be edited or deleted, because they are the tested, coherent definitions the system relies on. If you need something different, create a new role.

**Creating a role.** Give it a code, a name in both languages, a description, optionally a parent role to inherit from, and then tick the permissions. Sensitive permissions are marked as such so you can see what you are granting.

Think in terms of the job. Start from what the role genuinely needs, not from "administrator, minus a few things". That is how people quietly end up with authority nobody intended.

### 17.4 Audit log

**Address:** `/admin/audit`
**Permission needed:** view audit log (Auditor and Administrator)
**What it is for:** the permanent record of everything that has happened.

| Column | Meaning |
|---|---|
| Date | Exact date and time. |
| Performed by | Username and their role. |
| Actions | What kind of action, badged. |
| Record | Which record was affected. |
| Changes | A note and which fields changed. |
| IP | Which computer. |

**Action types:** Created, Updated, Posted, Cancelled, Stock movement, Price change, Permission change, Sign in, Failed sign in, Export.

Six of these are highlighted in orange because they change money, stock, or access: Delete, Permission change, Price change, Cancel, Lockout, and Failed sign in.

**Filter:** by action type. **Search:** across the entries.

**Nothing in the audit log can be changed or removed.** Not by you, not by an administrator, not by anyone with a login. This is enforced in several independent ways at once, including by the database itself, so that even a direct technical change is blocked.

**Verify log integrity.** This button (needs the verify permission) runs a check on the whole log.

Each entry carries a cryptographic fingerprint that includes the fingerprint of the entry before it, forming a chain. Change any historical entry and every later fingerprint stops matching.

- **Green: "Audit chain intact"** with the number of entries checked. Everything verifies.
- **Red: "Audit chain broken"** with the entry number where the chain fails and the reason.

A broken chain means the historical record has been tampered with. This is one of the most serious events the system can report. It must be escalated immediately, not investigated at leisure.

The check also runs automatically every night, and sends a critical alert if it fails.

**Sensitive fields are never written to the audit log.** Passwords and similar secrets are stripped before an entry is recorded, so the log can be read by auditors without exposing credentials.

---

## 18. Notifications

**Address:** `/notifications`
**Who sees it:** everyone. The bell in the top bar shows unread count and refreshes every minute.

### The screen

**Unread only / All** switches between showing everything and only what you have not read.

**Mark all as read** clears the badge.

Each notification shows an icon, a title, a category badge, the message, and the time. Unread ones have a coloured bar down the left side; read ones are dimmed.

Clicking a notification marks it as read. Many notifications are links that take you straight to the relevant screen with the right filters already applied.

### Severity

| Level | Icon | Meaning |
|---|---|---|
| **Information** | Blue circle | For your awareness. |
| **Warning** | Orange triangle | Needs attention soon. |
| **Critical** | Red triangle | Act now. |

### The notification types

| Type | Meaning | What to do |
|---|---|---|
| **Low stock** | A product has reached its reorder level. | Plan a purchase order. |
| **Out of stock** | A product has none left. | Order urgently. |
| **Expiring medicines** | Batches nearing expiry. | Opens the expiry report. Decide on a clearance. |
| **Expired medicines** | Batches now past expiry. | Remove from sellable stock and dispose. |
| **Purchase order approval request** | Someone raised an order needing your approval. | Opens the order. Review and approve or reject. |
| **Purchase order approved** | Your order was approved. | Send it to the supplier. |
| **Purchase order rejected** | Your order was rejected. | Read the reason, correct it, resubmit. |
| **Stock discrepancy** | **Critical.** Stock figures disagree with the ledger. | Opens Reconciliation. Escalate at once. |
| **Invoice due** | An invoice is due soon. | Contact the customer. |
| **Invoice overdue** | An invoice is past its due date. | Start collection. |
| **Credit limit reached** | A customer is at their limit. | Review before selling more on credit. |
| **Audit integrity failure** | **Critical.** The audit chain check failed. | Escalate immediately. |
| **Customer licence expiring** | A customer's pharmacy licence is running out. | Ask them for the renewal before it blocks sales. |
| **System announcement** | A message from an administrator. | Read it. |

The two critical ones, stock discrepancy and audit integrity failure, are different in kind from the rest. Everything else is an operational task. These two mean the system's own records may not be trustworthy, and they need escalating rather than working around.

**Duplicate alerts are suppressed.** If a product is out of stock for a week, you get one notification, not one every hour.

---

## 19. My profile

**Address:** `/profile`
**Who sees it:** everyone, for their own account. Reached by clicking your name at the bottom of the sidebar.

### General information

You may change only these fields, because they are the only ones your account can change about itself:

| Field | Notes |
|---|---|
| First name | Required. |
| Last name | Required. |
| Telephone | |
| Job title | |
| Language | Français or English. Also applies to your PDFs and emails. |

You cannot change your own roles or your own active status. Those are administered from the Users screen. This is a deliberate control: an account that could grant itself permissions would make the whole permission system pointless.

### Change password

Requires your current password, then the new one twice. The requirement is stated up front:

> *At least 12 characters, with uppercase, lowercase, digit and special character.*

As you type, a live checklist ticks off each rule.

The system will also reject a password that is too common, or too similar to your own name or email. Your last 5 passwords cannot be reused.

**When you change your password you are signed out of every device, including this one, and must sign in again.** This is deliberate. If you are changing your password because you think someone else has it, leaving their session running would defeat the point.

### Access and roles

Read-only. Shows your employee number, your last sign-in, and the roles you hold. If you need different access, this screen tells you exactly what to ask an administrator for.

---

# PART 3: REFERENCE

## 20. Every status word explained

**Invoice statuses**

| Status | Meaning |
|---|---|
| Draft | Created but not yet issued. Can still be changed. |
| Posted | Officially issued. Permanent. Cannot be edited or deleted. |
| Partially paid | Some money received, a balance remains. |
| Paid | Fully settled. |
| Overdue | Past its due date with money still owed. |
| Cancelled | Voided. |

**Invoice fiscal statuses (OBR declaration)**

Separate from the statuses above: these say whether the tax authority has the invoice, not whether the customer has paid. See section 12.4.

| Status | Meaning | Act? |
|---|---|---|
| Not declarable | Not sent to the OBR: proformas, and anything predating go-live. | No |
| Awaiting declaration | Queued. Goes out within a few minutes. | No |
| Declared to OBR | Accepted; registration number recorded and printed. | No |
| Rejected by OBR | Refused, and retrying has stopped. Fix the cause, then re-declare. | **Yes** |
| Cancellation declared | Cancelled, and the OBR has been told. | No |

**Sale statuses**

| Status | Meaning |
|---|---|
| Draft | Being built, stock not yet taken. |
| Confirmed | Completed, stock taken, invoice produced. |
| Delivered | Goods handed over. |
| Completed | Finished. |
| Returned | Fully returned. |
| Partially returned | Some lines came back. |
| Cancelled | Voided. |

**Purchase order statuses**

| Status | Meaning |
|---|---|
| Draft | Being prepared. |
| Pending approval | Waiting for someone with authority. |
| Approved | Authorised. |
| Rejected | Refused, with a reason. Can be corrected and resubmitted. |
| Sent | Sent to the supplier. |
| Partially received | Some goods arrived. |
| Received | All goods arrived. |
| Closed | Finished. |
| Cancelled | Abandoned. |

**Supplier invoice statuses**

What *you* owe a supplier, as opposed to what a customer owes you. See section 13.6.

| Status | Meaning |
|---|---|
| Draft | Being entered; not yet a live payable. |
| Awaiting payment | Recorded and owed; nothing paid yet. |
| Partially paid | Some money has gone against it. |
| Paid | Settled in full. |
| Overdue | Past its due date with a balance still owing. |
| Cancelled | Withdrawn. |

**Expense statuses**

See section 15.2.

| Status | Meaning | Editable? |
|---|---|---|
| Draft | Being entered. | Yes |
| Recorded | Booked as a cost incurred. | Yes |
| Approved | Signed off for payment. | No |
| Paid | Money has left the business. | No |
| Cancelled | Withdrawn, with a reason. | No |

**Batch statuses**

| Status | Sellable | Meaning |
|---|---|---|
| Active | Yes | Normal good stock. |
| Quarantined | No | Held, usually for a quality check. |
| Expired | No | Past its date. |
| Damaged | No | Physically unusable. |
| Recalled | No | Withdrawn by manufacturer or regulator. |
| Depleted | No | Used up. |
| Disposed | No | Destroyed and written off. |

**Customer and general statuses**

| Status | Meaning |
|---|---|
| Active | In normal use. |
| Inactive | Switched off but kept for history. |
| Blocked | Credit stopped. Cash sales may still be possible. |
| Suspended | A user account deliberately switched off. |
| Pending | Awaiting something. |
| Discontinued | A product no longer sold. |

---

## 21. Common error messages and what to do

| Message | What it means | What to do |
|---|---|---|
| *There is not enough stock to complete this operation.* | You asked for more than the available quantity. Remember that reserved stock is not available. | Check Stock levels for the available figure. Reduce the quantity, or order more. |
| *This batch has expired and cannot be used.* | The batch is past its expiry date. | Expired stock cannot be sold under any circumstances. It needs disposing of. |
| *This operation would exceed the customer's credit limit.* | The sale would push the customer over their limit. | Reduce the sale, collect payment on an old invoice first, or ask a supervisor with override authority. |
| *A discount of 15% exceeds the maximum of 10%.* | The discount you typed is above the ceiling that applies to everyone. | Reduce it to 10% or less. Nobody can authorise more — see section 4. If the customer has a negotiated rate above that, it belongs on their customer record as a standing discount. |
| *You do not have permission to apply a discount.* | Discounting is management authority. | Ask a Store Manager or an administrator. Do not try a smaller figure; it will be refused for the same reason. |
| *This action is not permitted in the document's current state.* | You are trying to do something out of sequence, for example receiving goods on an unapproved order. | Check the document's status and follow the correct sequence. |
| *This document is posted and can no longer be modified.* | You tried to change a finalised invoice. | Issue a credit note instead. |
| *A reason is required for this operation.* | You left a compulsory reason blank. | Enter a genuine reason. It will be read by someone one day. |
| *You cannot approve a purchase order that you raised.* | Separation of duties. | Ask a different authorised person to approve it. |
| *You do not have permission to perform this action.* | Your role does not include this. | Ask your administrator, and tell them exactly which action you need. |
| *The requested resource was not found.* | The record does not exist or was deleted. | Check the link or search for it again. |
| *The submitted data is invalid.* | One or more fields are wrong. | The specific errors appear beside the fields concerned. |
| *An unexpected error occurred. Please contact support.* | A fault in the system. | Note the time and what you were doing, and report it. |
| *Cannot reach the server. Check your connection.* | Network problem. | Check your internet connection and try again. Your unsaved work may be lost. |
| *The OBR rejected this declaration: …* | The tax authority refused the invoice, and the reason it gave follows. Usually a customer NIF that is missing or does not match their records. | Correct the cause, then click Declare to OBR on the invoice. Do not cancel and reissue. |
| *A NIF is required to post a credit invoice.* | A credit sale must identify the customer, because the invoice is a tax document. | Add the customer's NIF to their record, then post. |
| *The OBR system identifier is not configured.* | The system has not been given the identifier the OBR issues on approval, so signatures cannot be built. | A setup task, not a daily one. Tell your administrator. |
| *OBR declaration is not enabled in this environment.* | Electronic declaration is switched off. | Expected if you are not yet declaring electronically. Otherwise tell your administrator. |

### Things that look wrong but are not

These produce no error message, which is precisely why they cause confusion.

| What you see | Why | What to do |
|---|---|---|
| **I created a medicine and it has no stock.** | Correct. The catalogue records what you sell, not what you hold. A new product always starts at zero. | Book stock in on **Receive stock** (section 10.1) when the delivery arrives. |
| **There is no batch number field on the medicine form.** | Deliberate. A batch number is read off a physical carton, and you may not have one yet — a product is usually catalogued before its first delivery. | Enter the batch on **Receive stock**, where the carton is in front of you. |
| **I saved a new medicine and it took me to a receiving screen.** | A product is nearly always created because stock of it is about to arrive, so the system offers the next step with the product already selected. | If stock has not arrived yet, just leave the screen. Nothing is booked until you submit. |
| **The medicine's Batches card is empty.** | The product exists but nothing has been received against it. | Receive some stock, or check you are looking at the right product. |
| **My purchase order has no Edit button.** | It has been approved. An approval is somebody's signature on specific quantities and prices, so it can no longer be changed. | If it is wrong, cancel it and raise a new one. Drafts and rejected orders can still be edited. |
| **The supplier I want is not in the list on a purchase order.** | Only **approved** suppliers can be ordered from. | Ask someone with supplier permissions to approve them first. |
| **My invoice says "Awaiting declaration".** | Normal. Declarations go out every few minutes, not the instant you post. | Nothing. Only worth asking about if it stays that way for hours, which means the connection has been down that long. |
| **The invoice PDF has no OBR registration number.** | The OBR has not accepted it yet. Printing a registration number before the filing exists would be misleading. | Print again once the fiscal status reads "Declared to OBR". |
| **I cancelled an invoice but its fiscal status still says "Declared to OBR".** | Correct. The OBR has to be told separately, and that goes out in the background. | Nothing. It becomes "Cancellation declared" once the OBR confirms. |

---

## 22. Rules the system will never let you break

These are not settings. No permission, no role, and no administrator can switch them off.

1. **Expired stock cannot be sold.** Expired batches are excluded from selling at the deepest level of the system, not merely discouraged.
2. **Stock cannot go negative.** The system will not let you issue more than exists.
3. **The batch that expires soonest is used first (FEFO).** You do not choose batches when selling.
4. **A posted invoice cannot be edited or deleted.** Corrections are made with credit notes.
5. **Invoice numbers never skip.** The sequence is guaranteed to be gap-free, because a missing number looks to the tax authority like a concealed sale.
6. **The audit log cannot be changed.** Not by anyone, at any level, by any route.
7. **The stock ledger cannot be changed.** Corrections are added as new entries.
8. **You cannot approve your own purchase order.**
9. **A credit customer must have a NIF.**
10. **A stock adjustment must have a reason.**
11. **A credit limit override must have a reason, and it is recorded against you.**
12. **Stock cannot be received without a batch number and an expiry date.** This applies to every line, on every route into stock — against a purchase order, direct, or opening balances. There is no way to add stock without them.
13. **A supplier must be approved before it can be used on an order.**
14. **An approved purchase order cannot be edited.** Only drafts and rejected orders can be changed; anything else must be cancelled and re-raised.
15. **A batch number is never generated for you.** It is copied from the manufacturer's carton, because a batch number that does not match the printed lot code is useless in a recall.
16. **You cannot change your own roles.**
17. **Changing a password signs you out everywhere.**
18. **A manually entered discount can never exceed 10%.** There is no override permission and no senior user who can authorise more, because a limit that someone sufficiently senior can lift is not a limit. A customer's standing contractual discount is separate and is not capped. See section 4.
19. **An expense that has been approved or paid cannot be edited.** By then it is history. Cancel it with a reason instead.
20. **A supplier payment is never deleted.** Reversing one reopens the invoices it settled and recalculates their balances, leaving the reversal on the record.

If one of these blocks you, the rule is doing its job. The right response is to follow the proper route, not to look for a way around it.

---

## 23. Everyday tasks, step by step

### Make a cash sale

1. Sales → New sale.
2. Choose the customer.
3. Leave Sale type as Cash sale.
4. Check the warehouse.
5. Choose the product, enter the quantity. Add more lines as needed.
6. Check the total on the right.
7. Confirm sale.
8. You land on the invoice. Download PDF and print it.
9. Take the money, then Record payment.

### Make a credit sale

Requires the sell on credit permission.

1. Sales → New sale.
2. Choose the customer. Check available credit in the panel on the right.
3. Set Sale type to Credit sale.
4. Add the lines.
5. If the orange credit warning appears, either reduce the sale or get an override.
6. Confirm sale.
7. Download and print the invoice. The customer pays by the due date.

### Start selling a product you have never stocked before

This is two separate jobs, usually days apart. Doing the first does not give you any stock.

**When you decide to stock it:**

1. Catalogue → Medicines → **New**.
2. Fill in the name, category, unit, dosage form, cost and selling price.
3. Set the reorder level and storage conditions.
4. Save. **The product now exists with zero stock. This is normal.**

**When the goods actually arrive:**

5. Inventory → **Receive stock**.
6. Choose the product, then type the batch number and expiry **from the carton**, plus the quantity.
7. Submit. Now you have stock, and it is sellable.

If you are ordering through a supplier, step 4 is followed by raising a purchase order (below), and step 5 happens when the delivery turns up.

### Receive a delivery against a purchase order

Requires the receive goods permission.

1. Inventory → **Receive stock**. Leave the switch on **Against a purchase order**.
   (Or press **Receive goods** on the order itself — same screen, already filled in.)
2. Choose the order. The lines fill in automatically.
3. Enter the supplier's delivery note number.
4. Check the destination warehouse at the top.
5. For each line: check the quantity, and **type the batch number and expiry date exactly from the carton.**
6. Reduce the quantity on anything that arrived short; remove any line that did not arrive at all.
7. Fix anything listed under "Before you can continue".
8. Click **Receive stock**.

### Receive a delivery with no purchase order

For an emergency supply or a donation. Requires the receive stock permission.

1. Inventory → **Receive stock**. Set the switch to **Direct delivery**.
2. Choose the destination warehouse and enter the delivery note number if there is one.
3. On each line, search for the product. **If it is not in the catalogue, choose ＋ New medicine** and create it there — you will not lose the delivery.
4. **Type the batch number and expiry date exactly from the carton.** Enter the quantity and the unit cost.
5. **Add a line** for each further product in the delivery.
6. Click **Receive stock**. The whole delivery is booked together, or none of it is.

### Enter the stock you already hold (first-time setup)

Do this once, when the system goes live. Requires the receive stock permission.

1. Inventory → **Receive stock** → **Direct delivery**.
2. Tick **Opening stock (initial inventory)**.
3. Add one line per batch on your shelves — **not** one line per product. If you hold three different lots of the same medicine, that is three lines, each with its own batch number and expiry.
4. Enter the quantity you counted and what you paid per unit.
5. Click **Receive stock**.

Ticking Opening stock records these as opening balances rather than purchases, so your first month's purchasing figures are not inflated by the whole starting inventory.

### Raise a purchase order

Requires the raise purchase orders permission.

1. Purchasing → Purchase orders → **New purchase order**.
2. Choose an **approved** supplier and the delivery warehouse. If the supplier is not listed, they must be approved first.
3. Set the expected delivery date, or leave it blank to use the supplier's usual lead time.
4. Add each product with its quantity. The supplier unit cost is pre-filled from the catalogue — replace it with what the supplier actually quoted. Use **＋ New medicine** if something is not catalogued yet.
5. Set a **minimum acceptable expiry** on any line where short-dated stock would be a problem. Anything shorter is refused when the delivery arrives.
6. Add freight, customs duty, and other charges if you know them.
7. Click **Save and submit** to send it for approval, or **Save draft** to finish it later.
8. Someone else approves it — you cannot approve your own — then marks it as sent.

### Correct a rejected purchase order

1. Open the rejected order. The reason is in a red banner at the top.
2. Click **Edit**.
3. Fix what the approver objected to.
4. Click **Save and submit** to send it back for approval.

An **approved** order cannot be edited. If an approved order is wrong, cancel it and raise a new one.

### Approve a purchase order

Requires approve purchase order.

1. Open the notification, or go to Purchasing and filter by Pending approval.
2. Open the order. Read the lines, the totals, and the landed cost.
3. Approve, adding any notes. Or Reject with a clear reason.
4. If the Approve button is greyed out, you raised this order yourself. Someone else must approve it.

### Record a customer payment

Requires record payment.

**For one specific invoice:** open the invoice, click Record payment, adjust the amount if it is a part payment, choose the method, add the bank reference, confirm.

**For a general payment across several invoices:** Invoicing → Payments → Record payment. Choose the customer and enter the amount. The money is applied to the oldest invoices first.

The payment receipt for any invoice that reaches zero is issued automatically — you do not raise it.

### Record a bill from a supplier

Requires record supplier invoice.

1. Purchasing → Supplier invoices → **New**.
2. Choose the supplier and type **their** invoice number exactly as printed on the document.
3. If it bills a purchase order, link that order. Leave it blank for freight, clearing, or anything else that had no order.
4. Enter the invoice date and the due date, then the amounts: subtotal, VAT, and freight, duty and other charges as billed.
5. Save. The invoice becomes a payable and appears in the supplier's outstanding balance.

### Pay a supplier

Requires record supplier payment.

1. Purchasing → Supplier payments → **New**.
2. Choose the supplier. Their open invoices are listed.
3. Enter the amount, the date the money actually left, the method, and the reference (cheque number, transfer reference, mobile money code).
4. Leave allocation on **Automatic** to settle the oldest invoices first — the screen shows you which ones before you confirm. Switch to **Manual** only if the payment deliberately splits across specific bills.
5. Confirm. Anything unallocated stays as credit on account with that supplier.

### Record an operating cost

Requires record expense. Pharmacists and above.

1. Accounting → Expenses → **New**.
2. Choose the category (rent, salaries, fuel, and so on) and describe what the cost was for.
3. Set the **expense date** — when the cost was incurred, not when you paid it.
4. Enter the amount as it reads on the receipt, VAT included. Fill in the VAT figure separately only if you know it and can reclaim it.
5. Enter who was paid. Link a supplier or purchase order if the cost relates to one, such as freight on a consignment.
6. Save. Use the notes field if the cost needs explaining.

When it is actually paid, set the **date paid** and mark it Paid — that is the date the cash outflow report uses.

### See where the money went last month

Requires view accounting reports.

- **Accounting → Overview** for the whole picture: revenue, cost of goods, expenses, and the operating result.
- **Accounting → Reports → Expenses by category** for what you spent it on.
- **Accounting → Reports → Cash outflow** for what actually left the bank, supplier payments and expenses side by side.

Remember the overview's operating result is an indicator, not a statutory profit figure.

### Do a stock count adjustment

Requires adjust stock.

1. Inventory → Batches. Find the batch.
2. Click the slider icon.
3. Enter the counted quantity. The difference appears.
4. Enter a specific reason.
5. Confirm.

### Handle a customer return

Requires process return.

1. Find the original sale.
2. Start a return, choosing the lines and quantities coming back.
3. Enter the reason.
4. Decide carefully whether to tick **Return to sellable stock**. Only tick it if you are confident storage conditions were maintained.
5. Confirm. A credit note is created, reducing what the customer owes.

### Check what is expiring

1. Inventory → Expiry report.
2. Choose a horizon, for example 90 days.
3. Read the **value at risk** at the top right.
4. Sort out the most urgent lines: run a clearance, contact customers who use them, or plan disposal.

### Handle a recall

1. Get the batch number from the manufacturer or regulator.
2. Search for it in Inventory → Batches. Set its status to Recalled so it can no longer be sold.
3. Ask an administrator or manager to run the recall trace on that batch number, which returns every customer who received units from it, with quantities and dates.
4. Contact every one of them.
5. Handle returns and credit notes as the goods come back.

### Add a new customer

Requires create customer.

1. Partners → Customers → New.
2. Enter the business name and type.
3. **If they will buy on credit, the NIF is compulsory.**
4. Enter contact details.
5. Set payment terms and, if creating on credit, the credit limit.
6. Save. Remember that the credit limit can only be changed afterwards through the separate credit limit action with a reason.

### Add a new product

Requires create medicine.

1. Catalogue → Medicines → New.
2. Fill in identification: name, dosage form, category, unit.
3. Enter reference cost, selling price, and VAT rate.
4. Set the reorder level and safety stock.
5. Choose the storage conditions.
6. Tick prescription required and controlled substance where they apply.
7. Save. The product code is allocated automatically.

### Investigate a stock discrepancy

1. Inventory → Reconciliation.
2. If it is green, there is nothing to investigate.
3. If it is red, note the batch numbers and the differences.
4. **Report it to your manager and to system support immediately.**
5. Do not carry on posting movements on the affected batches until it is explained.

---

## Getting help

**If a button is missing or greyed out.** It is almost always a permission. Your profile page lists your roles. Tell your administrator which action you need, not just "I need more access".

**If the system refuses something.** Read the message. The rules in section 22 exist for regulatory and financial reasons, and the right answer is the proper route, not a workaround.

**If a figure looks wrong.** Check Stock levels for available versus remaining, check Payments for unallocated money, and check the Movements ledger, which shows every change with who made it.

**If you get a critical notification.** This means a stock discrepancy or an audit integrity failure. Escalate straight away.

**If something is broken.** Note the time, the screen, and what you were doing, and report it.
