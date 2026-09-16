const knex = require('knex');

exports.up = async function (knex) {
    // Create the `users` table
    await knex.schema.createTable('currency', (table) => {
        table.increments('id').primary();
        table.string('name');
        table.boolean('status').defaultTo(false);
        table.string('unit').nullable();
    });

};

exports.down = async function (knex) {
    // Drop the `users` table
    await knex.schema.dropTableIfExists('currency');
};