const knex = require('knex');

exports.up = async function (knex) {
    // Create the `users` table
    await knex.schema.createTable('taxes', (table) => {
        table.increments('id').primary(); // Auto-incrementing ID
        table.string('name').notNullable().index();
        table.string('amount').defaultTo(0);
        table.boolean('status').defaultTo(true);
    });

};

exports.down = async function (knex) {
    // Drop the `users` table
    await knex.schema.dropTableIfExists('taxes');
};

