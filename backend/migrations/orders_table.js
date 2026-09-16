const knex = require('knex');

exports.up = async function (knex) {

    await knex.schema.createTable('orders', (table) => {
        table.increments('id').primary(); // Auto-incrementing ID
        table.bigInteger('customer_id'); 
        table.bigInteger('cashier_id'); 
        table.string('amount').nullable();
        table.string('ref_no');
        table.string('session_id').nullable();
        table.string('pos_shop');
        table.string('receipt_number').nullable();
        table.string('payment_mode').defaultTo('cash');
        table.string('transaction_type').defaultTo('cash'); // credit karna tha isko
        table.timestamps(true,true);
    });

};

exports.down = async function (knex) {
    // Drop the `users` table
    await knex.schema.dropTableIfExists('orders');
};

 