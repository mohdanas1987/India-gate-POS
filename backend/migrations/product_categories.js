const knex = require('knex');

exports.up = async function (knex) {
    // Create the `users` table
    await knex.schema.createTable('products_categories', (table) => {
        table.increments('id').primary(); // Auto-incrementing ID
        table.string('name').notNullable().index();
        table.string('color').defaultTo('#1f1d1d');
        table.boolean('status').defaultTo(true);
    });

};

exports.down = async function (knex) {
    // Drop the `users` table
    await knex.schema.dropTableIfExists('products_categories');
};

