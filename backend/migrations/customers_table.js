const knex = require('knex');

exports.up = async function (knex) {
    // Create the `customers` table
    await knex.schema.createTable('customers', (table) => {
        table.increments('id').primary(); // Auto-incrementing ID
        table.string('name').notNullable();
        table.string('phone').notNullable().unique();
        table.string('email').nullable();
        table.string('title').nullable();
        table.string('street').nullable();
        table.string('state').nullable();
        table.string('city').nullable();
        table.longText('notes').nullable();
    });

};

exports.down = async function (knex) {
    // Drop the `users` table
    await knex.schema.dropTableIfExists('customers');
};

 