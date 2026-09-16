const knex = require('knex');

exports.up = async function (knex) {

    await knex.schema.createTable('cash_register', (table) => {
        table.increments('id').primary(); // Auto-incrementing ID
        table.string('opening_cash').nullable();
        table.string('closing_cash');
        table.string('date');
        table.boolean('status').defaultTo(true);
        table.bigInteger('user_id');
    });

};

exports.down = async function (knex) {
    // Drop the `users` table
    await knex.schema.dropTableIfExists('cash_register');
};

 