/**
 * @param { import("knex").Knex } knex
 * @returns { Promise<void> }
 */
exports.up = function(knex) {
    return knex.schema.alterTable('orders', function(table) {
        table.dropColumn('ref_no');
        table.dropColumn('pos_shop');
        table.dropColumn('receipt_number');
        table.dropColumn('customer_id');
    });
};

/**
 * @param { import("knex").Knex } knex
 * @returns { Promise<void> }
 */
exports.down = function(knex) {
    return knex.schema.alterTable('orders', function(table) {
        table.string('ref_no');
        table.string('pos_shop');
        table.string('receipt_number');
        table.bigInteger('customer_id');
    });
};
