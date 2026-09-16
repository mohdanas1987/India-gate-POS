const knex = require('knex');

exports.up = async function (knex) {
    // Create the `users` table
    await knex.schema.createTable('users', (table) => {
        table.increments('id').primary(); // Auto-incrementing ID
        table.string('name').notNullable();
        table.string('email').notNullable().unique();
        table.string('password').notNullable();
        table.string('type').defaultTo('cashier');
        table.boolean('verified').defaultTo(false);
        table.boolean('status').defaultTo(false);
        table.timestamp('email_verified_at').nullable();
        table.string('remember_token').nullable();
        table.timestamps(true, true); // Created at & Updated at
    });

};

exports.down = async function (knex) {
    // Drop the `users` table
    await knex.schema.dropTableIfExists('users');
};
