import time

from odoo import models, fields, api, _
from odoo.exceptions import UserError
from odoo.fields import Datetime

class SaleOrder(models.Model):
    _inherit = 'sale.order'

    # CON UNA NOTA DE CREDITO DESDE CERO (NO SE ASOCIA A LA ORDEN)
    def _reprocess_from_global_invoice_non_linked(self, global_invoice):
        self.ensure_one()

        # HACER LAS MISMAS VALIDACIONES DE SUPER CLASE O VER LA MANERA DE QUE ENCTREN PRIMERO

        if not global_invoice or not global_invoice.l10n_mx_edi_cfdi_uuid:
            raise UserError(_("La factura global no está timbrada."))

        # Paso 1: Identificar productos de la orden
        order_product_ids = self.order_line.mapped('product_id.id')

        # Paso 2: Filtrar líneas de la factura global que coincidan con los productos
        lines_to_refund = global_invoice.invoice_line_ids.filtered(
            lambda l: l.product_id.id in order_product_ids
        )

        inv_usage = global_invoice.l10n_mx_edi_usage
        inv_uuid_origin = global_invoice.l10n_mx_edi_cfdi_uuid
        l10n_mx_edi_payment_method_id = global_invoice.l10n_mx_edi_payment_method_id.id

        if not lines_to_refund:
            raise UserError(_("No se encontraron líneas en la factura global que coincidan con los productos de la orden."))

        # Paso 3: Crear nota de crédito parcial manualmente
        move = self.env['account.move']
        refund_vals = {
            'move_type': 'out_refund',
            'partner_id': global_invoice.partner_id.id,
            'invoice_origin': f"Devolución parcial por {self.name}",
            'invoice_date': fields.Date.today(),
            'date': fields.Date.today(),
            'invoice_line_ids': [],

            'l10n_mx_edi_usage': inv_usage,
            'l10n_mx_edi_origin': f"01|{global_invoice.l10n_mx_edi_cfdi_uuid}",
            'l10n_mx_edi_payment_method_id': l10n_mx_edi_payment_method_id,
        }

        for line in lines_to_refund:
            # Obtener la cantidad vendida de este producto en la orden original
            so_qty = sum(
                self.order_line.filtered(lambda l: l.product_id.id == line.product_id.id).mapped('product_uom_qty'))

            # Evitar generar líneas sin cantidad
            if so_qty <= 0:
                continue

            refund_vals['invoice_line_ids'].append((0, 0, {
                'product_id': line.product_id.id,
                'name': line.name,
                'quantity': so_qty,
                'price_unit': line.price_unit,
                'tax_ids': [(6, 0, line.tax_ids.ids)],
                'analytic_account_id': line.analytic_account_id.id if line.analytic_account_id else False,
                'analytic_tag_ids': [(6, 0, line.analytic_tag_ids.ids)],
            }))

        refund = move.create(refund_vals)
        print(refund_vals)
        print(refund)

        return 0

        # Aqui debemos retornar a super para seguir con el proceso ya existente.


        # Paso 4: Crear factura nueva personalizada
        new_invoice = self._create_invoices()
        new_invoice.write({
            'partner_id': self.partner_id.id,
            'invoice_origin': f'Refacturación de {self.name}',
        })

        return {
            'status': 'refacturado',
            'new_invoice_id': new_invoice.id,
            'refund_id': refund.id,
        }

    def _reprocess_from_global_invoice_bkp(self, global_invoice):
        self.ensure_one()
        print("⚙️ Iniciando reprocesamiento de orden %s", self.name)

        # Filtramos solo una factura global válida (factura publicada, público general)
        global_invoice = global_invoice.filtered(lambda m: (
                m.move_type == 'out_invoice' and
                m.state == 'posted' and
                m.partner_id.vat == 'XAXX010101000'
        ))
        if not global_invoice:
            raise UserError(_("No hay una factura global válida y publicada."))

        global_invoice = global_invoice[0]  # Asegura singleton

        print("🧾 Generando nota de crédito parcial desde factura global %s", global_invoice.name)

        # Productos de la orden
        order_lines_by_product = {
            line.product_id.id: line.product_uom_qty for line in self.order_line
        }

        print(order_lines_by_product)

        # Crear la NC con todas las líneas (luego eliminaremos las que no aplican)
        reversal_wizard = self.env['account.move.reversal'].create({
            'move_ids': [(6, 0, [global_invoice.id])],
            'reason': f"Reembolso parcial por orden {self.name}",
            'refund_method': 'refund',
            'date': fields.Date.today(),
            'journal_id': global_invoice.journal_id.id,
        })

        reversal_result = reversal_wizard.reverse_moves()
        refund_id = reversal_result.get('res_id') or reversal_result.get('res_ids', [False])[0]
        refund = self.env['account.move'].browse(refund_id).with_context(check_move_validity=False)

        lines_kept = []
        for line in refund.invoice_line_ids:
            print('line: ', line, line.product_id.id)
            if line.product_id.id in order_lines_by_product:
                qty_so = order_lines_by_product[line.product_id.id]
                print('qty_so: ', qty_so)
                if qty_so > 0:
                    line.quantity = qty_so
                    lines_kept.append(line.id)
        print('lines_kept: ', lines_kept)

        # Eliminar líneas que no son parte de la SO
        refund.invoice_line_ids.filtered(lambda l: l.id not in lines_kept).unlink()

        # Validar que queden líneas
        if not refund.invoice_line_ids:
            raise UserError(_("La nota de crédito no tiene líneas válidas después de filtrar por productos."))

        # Metadata fiscal
        refund.write({
            'partner_id': self.partner_id.id,
            'invoice_origin': f"Nota de crédito por refacturación de {self.name}",
            'ref': f"NC parcial - {self.name}",
            'l10n_mx_edi_usage': global_invoice.l10n_mx_edi_usage,
            'l10n_mx_edi_origin': f"01|{global_invoice.l10n_mx_edi_cfdi_uuid}",
            'l10n_mx_edi_payment_method_id': global_invoice.l10n_mx_edi_payment_method_id.id,
        })

        refund.action_post()
        print("✅ Nota de crédito %s generada y publicada", refund.name)

        return refund

    def _reprocess_from_global_invoice(self, global_invoice):
        self.ensure_one()
        print("⚙️ Iniciando reprocesamiento de orden %s", self.name)

        # Verificar si ya existe una nota de crédito asociada a esta SO
        existing_refund = self.invoice_ids.filtered(
            lambda m: m.move_type == 'out_refund' and m.state in ('draft', 'posted')
        )

        if existing_refund:
            print("Ya existe una nota de crédito asociada a esta orden.")
            raise UserError(_("Ya existe una nota de crédito asociada a esta orden."))


        # Filtrar solo facturas válidas (factura global)
        global_invoice = global_invoice.filtered(lambda m: (
                m.move_type == 'out_invoice' and
                m.state == 'posted' and
                m.partner_id.vat == 'XAXX010101000'
        ))
        if not global_invoice:
            raise UserError(_("No hay una factura global válida y publicada."))

        global_invoice = global_invoice[0]

        print("🧾 Generando nota de crédito parcial desde factura global %s", global_invoice.name)

        # Crear nota de crédito parcial
        reversal_wizard = self.env['account.move.reversal'].create({
            'move_ids': [(6, 0, [global_invoice.id])],
            'reason': f"Reembolso parcial por orden {self.name}",
            'refund_method': 'refund',
            'date': fields.Date.today(),
            'journal_id': global_invoice.journal_id.id,
        })

        reversal_result = reversal_wizard.reverse_moves()
        refund_id = reversal_result.get('res_id') or (reversal_result.get('res_ids') or [False])[0]
        refund = self.env['account.move'].browse(refund_id).with_context(check_move_validity=False)

        # Mapeo de productos y cantidades de la SO
        order_product_qty = {
            line.product_id.id: line.product_uom_qty
            for line in self.order_line
        }
        print('order_product_qty: ', order_product_qty)

        # -------------------------------------------------------------------------------
        # Determinar líneas a conservar. Si hay lineas de factura repetidas, solo tomar las primeras que coinciden con el producto y cantidad originales de la SO
        kept_lines = self.env['account.move.line']
        product_remaining_qty = dict(order_product_qty)  # Copia mutable

        for line in refund.invoice_line_ids:
            if line.display_type:
                kept_lines |= line
                continue

            pid = line.product_id.id
            if pid not in product_remaining_qty:
                continue

            remaining_qty = product_remaining_qty[pid]

            if remaining_qty <= 0:
                continue

            if line.quantity > remaining_qty:
                line.quantity = remaining_qty
                product_remaining_qty[pid] = 0
            else:
                product_remaining_qty[pid] -= line.quantity

            kept_lines |= line

        # -------------------------------------------------------------------------------

        # Eliminar solo líneas no necesarias
        to_delete = refund.invoice_line_ids - kept_lines

        for l in to_delete:
            print(f"Eliminando línea: {l.product_id.name} - {l.display_type}")

        to_delete.unlink()

        # IMPORTANTE: Recalcular líneas dinámicas para actualizar impuestos
        refund._recompute_dynamic_lines(recompute_all_taxes=True)

        # Validar que aún haya líneas válidas
        if not refund.invoice_line_ids:
            raise UserError(_("La nota de crédito quedó sin líneas tras filtrar por productos de la orden."))

        # Información fiscal y campos relacionados
        local_now = Datetime.context_timestamp(self, Datetime.now()).replace(second=0, microsecond=0)


        print(local_now)

        refund.write({
            'auto_post': False,
            'date': local_now,
            'invoice_date': local_now.date(),
            #'invoice_date_due': local_now,
            'partner_id': global_invoice.partner_id.id,
            'invoice_origin': f"Nota de crédito por refacturación de {self.name}",
            'ref': f"NC parcial para: {self.name}",
            'l10n_mx_edi_usage': global_invoice.l10n_mx_edi_usage,
            'l10n_mx_edi_origin': f"01|{global_invoice.l10n_mx_edi_cfdi_uuid}",
            'l10n_mx_edi_payment_method_id': global_invoice.l10n_mx_edi_payment_method_id.id,
            'from_autoinvoice': True,
        })

        print("Fecha final de la NC:", refund.date, refund.invoice_date)

        # Confirmar
        refund.action_post()

        message = {
            'body': f"<p>Nota de crédito generada automáticamente por refacturación de la orden <b>{self.name}<b> a partir de la factura global <b>{global_invoice.name}</b>.</p>",
            'message_type': 'notification',
            'subtype_id': self.env.ref('mail.mt_note').id,
        }

        print(refund.id, message)
        refund.message_post(**message)

        return refund
