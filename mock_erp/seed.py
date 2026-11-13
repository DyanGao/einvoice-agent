"""Fill the mock ERP with the synthetic matching world.

    python -m mock_erp.seed

Replaces whatever the ERP database held, and writes the world's files with
the ground truth to eval/out/world for the eval. The ground truth is not
loaded into the ERP.
"""

import argparse
from pathlib import Path

from sqlalchemy import make_url
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from eval.generators.world import DEFAULT_SEED, World, build_world, write_world
from mock_erp import db


def load(world: World, engine: Engine) -> None:
    """Replace the ERP's content with this world."""
    db.Base.metadata.drop_all(engine)
    db.Base.metadata.create_all(engine)
    with Session(engine) as session, session.begin():
        for supplier in world.suppliers:
            company = supplier.company
            session.add(
                db.SupplierRow(
                    id=supplier.id,
                    name=company.name,
                    vat_id=company.vat_id,
                    street=company.street,
                    postcode=company.postcode,
                    city=company.city,
                    email=company.email,
                    ibans=[
                        db.SupplierIbanRow(position=position, iban=iban)
                        for position, iban in enumerate(supplier.ibans)
                    ],
                )
            )
        for order in world.orders:
            session.add(
                db.PurchaseOrderRow(
                    number=order.number,
                    supplier_id=order.supplier_id,
                    order_date=order.order_date,
                    lines=[
                        db.OrderLineRow(
                            position=line.position,
                            description=line.description,
                            quantity=str(line.quantity),
                            unit_code=line.unit_code,
                            unit_price_cents=db.cents(line.unit_price),
                            vat_rate=str(line.vat_rate),
                        )
                        for line in order.lines
                    ],
                )
            )
        for delivery in world.deliveries:
            session.add(
                db.DeliveryRow(
                    id=delivery.id,
                    po_number=delivery.po_number,
                    delivery_date=delivery.delivery_date,
                    lines=[
                        db.DeliveryLineRow(
                            po_position=line.po_position, quantity=str(line.quantity)
                        )
                        for line in delivery.lines
                    ],
                )
            )
        for transaction in world.bank_transactions:
            session.add(
                db.BankTransactionRow(
                    id=transaction.id,
                    booking_date=transaction.booking_date,
                    amount_cents=db.cents(transaction.amount),
                    counterparty_name=transaction.counterparty_name,
                    counterparty_iban=transaction.counterparty_iban,
                    purpose=transaction.purpose,
                )
            )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--out", type=Path, default=Path("eval/out/world"))
    args = parser.parse_args()
    url = db.database_url()
    file = make_url(url).database
    if url.startswith("sqlite") and file and file != ":memory:":
        Path(file).parent.mkdir(parents=True, exist_ok=True)
    world = build_world(args.seed)
    load(world, db.engine_for(url))
    write_world(world, args.out)
    print(
        f"mock ERP seeded: {len(world.suppliers)} suppliers, {len(world.orders)} orders, "
        f"{len(world.deliveries)} deliveries, {len(world.bank_transactions)} bank transactions; "
        f"{len(world.invoices)} invoices with ground truth in {args.out}"
    )


if __name__ == "__main__":
    main()
