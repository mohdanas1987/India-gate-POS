const knex = require('knex');

exports.up = async function (knex) {
    await knex.schema.dropTableIfExists('cashier_sessions');
    await knex.schema.createTable('cashier_sessions', (table) => {
        table.increments('id').primary(); // Auto-incrementing ID
        table.bigInteger('order_id').nullable();
        table.bigInteger('cashier_id');
        table.bigInteger('session_id'); 
        table.bigInteger('cash_register_id');
        table.json('data');
    });

};

exports.down = async function (knex) {
    // Drop the `users` table
    await knex.schema.dropTableIfExists('cashier_sessions');
};

 