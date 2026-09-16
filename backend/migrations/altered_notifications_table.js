const knex = require('knex');

exports.up = async function (knex) {
    await knex.schema.dropTableIfExists('web_notifications');
    await knex.schema.createTable('web_notifications', (table) => {
        table.increments('id').primary(); // Auto-incrementing ID
        table.string('content'); 
        table.boolean('read').defaultTo(false);
        table.bigInteger('user_id');
        table.timestamps(true);
    });

};

exports.down = async function (knex) {
    // Drop the `users` table
    await knex.schema.dropTableIfExists('web_notifications');
};