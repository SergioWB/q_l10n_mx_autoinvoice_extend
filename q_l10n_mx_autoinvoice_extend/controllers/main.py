from odoo import http, _
from odoo.http import request
from odoo.addons.q_l10n_mx_autoinvoice.controllers.main import Autoinvoice

class AutoinvoiceExtended(Autoinvoice): # Heredo de la clase Autoinvoice original

    @http.route('/q_l10n_mx_autoinvoice/order', type='json', auth='public', website=True, csrf=False)
    def autoinvoice_order_backup(self, number_order=False, amount_total=0):
        order = request.env['sale.order'].sudo().search([('name', '=', number_order)], limit=1)

        if order and order.invoice_ids:
            global_invoice = order.invoice_ids.filtered(
                lambda inv: inv.partner_id.vat == 'XAXX010101000' and inv.partner_id.name == 'PÚBLICO EN GENERAL'
            )
            if global_invoice:
                return order._reprocess_from_global_invoice(global_invoice)

        # De lo contrario, flujo original
        return super().autoinvoice_order(number_order=number_order, amount_total=amount_total)

    @http.route('/q_l10n_mx_autoinvoice/order', type='json', auth='public', website=True, csrf=False)
    def autoinvoice_order_backup_2(self, number_order=False, amount_total=0):
        user_root = request.env.ref('base.user_root')
        res_config_settings = request.env['res.config.settings'].sudo().with_user(user_root).get_values()

        order = request.env['sale.order'].sudo().with_user(user_root).search([
            ('name', '=', number_order),
            ('company_id', '=', request.env.user.company_id.id)
        ])

        if not order and res_config_settings['autoinvoice_mercadolibre']:
            order = request.env['sale.order'].sudo().with_user(user_root).search([
                '|',
                ('name', '=', "ML {0}".format(number_order)),
                ('meli_order_id', '=', number_order),
                ('company_id', '=', request.env.user.company_id.id)
            ])

        if not order:
            return {'error': _('No se encontró la orden de venta.')}

        difference = abs(float(order.amount_total) - float(amount_total))
        if difference > float(res_config_settings['autoinvoice_tolerance']):
            return {'error': _('Not exist order with these records.')}

        if order.state not in ('sale', 'done'):
            return {'error': _('The order is not confirmed.')}

        if order.invoice_ids:
            # Validar si ya fue refacturada a cliente final
            non_global_invoice = order.invoice_ids.filtered(
                lambda
                    inv: inv.partner_id.vat != 'XAXX010101000' and inv.move_type == 'out_invoice' and inv.state == 'posted'
            )
            if non_global_invoice:
                return {
                    'error': _(f'La orden ya ha sido facturada a un cliente específico. RFC: {non_global_invoice[0].partner_id.vat}.')
                }

            # Despues, si aun no fue refacturada a cliuente final, ver si esta en una factura global
            global_invoice = order.invoice_ids.filtered(
                lambda inv: inv.partner_id.vat == 'XAXX010101000' and (inv.partner_id.name == 'PÚBLICO EN GENERAL')
                            and inv.move_type == 'out_invoice' and inv.state == 'posted'
            )

            # -----------------------------------------------------------------
            # Verificar si ya existe una NC con origen esta orden
            existing_refund = self.env['account.move'].search([
                ('move_type', '=', 'out_refund'),
                ('invoice_origin', 'ilike', self.name),
                ('state', '=', 'posted'),
                ('reversed_entry_id', '=', global_invoice.id)
            ])

            if existing_refund:
                return {
                    'error': _("Ya se generó una nota de crédito para esta orden.")
                }

            # -----------------------------------------------------------------

            if global_invoice:
                # Primero se crea la nota de credito
                order._reprocess_from_global_invoice(global_invoice[0])

                # Desde la SO, se crea la factura
                new_invoice = order._create_invoices()
                new_invoice.write({'ref': f"Factura cliente por refacturación de {order.name}"})

                # Paso 3: Continuar flujo normal mostrando el formulario
                template = request.env['ir.ui.view']._render_template('q_l10n_mx_autoinvoice.address', {
                    'country_id': request.env.ref('base.mx'),
                })
                return {
                    'order_id': order.id,
                    'invoice_id': new_invoice.id,
                    'template': template,
                }

                # ****************************************************************


        elif order.invoice_ids and order.invoice_ids[0].l10n_mx_edi_cfdi_uuid:
            print("_render_template('q_l10n_mx_autoinvoice.download'")
            return {
                'template': request.env['ir.ui.view']._render_template('q_l10n_mx_autoinvoice.download', {
                    'invoice_id': order.invoice_ids[0].id,
                })
            }

        else:
            template = request.env['ir.ui.view']._render_template('q_l10n_mx_autoinvoice.address', {
                'country_id': request.env.ref('base.mx'),
            })
            print("_render_template('q_l10n_mx_autoinvoice.address'")
            return {
                'order_id': order.id,
                'template': template,
            }

    @http.route('/q_l10n_mx_autoinvoice/order', type='json', auth='public', website=True, csrf=False)
    def autoinvoice_order(self, number_order=False, amount_total=0):
        user_root = request.env.ref('base.user_root')
        res_config_settings = request.env['res.config.settings'].sudo().with_user(user_root).get_values()

        order = request.env['sale.order'].sudo().with_user(user_root).search([
            ('name', '=', number_order),
            ('company_id', '=', request.env.user.company_id.id)
        ])

        if not order and res_config_settings['autoinvoice_mercadolibre']:
            order = request.env['sale.order'].sudo().with_user(user_root).search([
                '|',
                ('name', '=', f"ML {number_order}"),
                ('meli_order_id', '=', number_order),
                ('company_id', '=', request.env.user.company_id.id)
            ])

        if not order:
            return {'error': _('No se encontró la orden de venta.')}

        if abs(float(order.amount_total) - float(amount_total)) > float(res_config_settings['autoinvoice_tolerance']):
            return {'error': _('Not exist order with these records.')}

        if order.state not in ('sale', 'done'):
            return {'error': _('The order is not confirmed.')}

        # Validación que ya está facturada a cliente final
        non_global_invoice = order.invoice_ids.filtered(
            lambda inv: inv.partner_id.vat != 'XAXX010101000' and inv.move_type == 'out_invoice' and inv.state == 'posted'
        )
        if non_global_invoice:
            return {
                'error': _(f'La orden ya fue facturada a cliente final. RFC: {non_global_invoice[0].partner_id.vat}.')
            }

        # Verificar factura global
        global_invoice = order.invoice_ids.filtered(
            lambda inv: inv.partner_id.vat == 'XAXX010101000' and
                        inv.partner_id.name.lower() == 'público en general' and
                        inv.move_type == 'out_invoice' and inv.state == 'posted'
        )

        # Verificar si ya tiene nota de crédito
        if global_invoice:
            existing_refund = request.env['account.move'].sudo().search([
                ('move_type', '=', 'out_refund'),
                ('invoice_origin', 'ilike', order.name),
                ('state', '=', 'posted'),
                ('reversed_entry_id', 'in', global_invoice.ids)
            ])
            if existing_refund:
                return {'error': _("Ya se generó una nota de crédito para esta orden.")}

            # Creamos la nota de crédito
            order._reprocess_from_global_invoice(global_invoice[0])

        # Mostramos el formulario para que el cliente capture su dirección
        template = request.env['ir.ui.view']._render_template('q_l10n_mx_autoinvoice.address', {
            'country_id': request.env.ref('base.mx'),
        })

        return {
            'order_id': order.id,
            'template': template,
        }

    @http.route('/q_l10n_mx_autoinvoice/select_address', type='json', auth='public', website=True, csrf=False)
    def autoinvoice_select_address(self, order_id, partner_id):
        user_root = request.env.ref('base.user_root')
        try:
            order = request.env['sale.order'].sudo().with_user(user_root).search([
                ('id', '=', int(order_id)),
                ('company_id', '=', request.website.company_id.id)
            ])
            order.write({
                'partner_invoice_id': int(partner_id),
            })

            # CREAR FACTURA NUEVA DESDE CERO
            invoice = order._create_invoices()
            invoice.write({
                'partner_id': int(partner_id),
                'ref': f"Factura cliente por refacturación de {order.name}",
            })

            template = request.env['ir.ui.view']._render_template(
                'q_l10n_mx_autoinvoice.additional_information')

            message = {
                'body': f"<p>Factura creada automáticamente a petición del cliente de la orden <b>{order.name}</b>.</p>",
                'message_type': 'notification',
                'subtype_id': request.env.ref('mail.mt_note').id,
            }

            invoice.message_post(**message)

            return {
                'invoice_id': invoice.id,
                'template': template,
            }
        except Exception as error:
            return {'error': str(error)}