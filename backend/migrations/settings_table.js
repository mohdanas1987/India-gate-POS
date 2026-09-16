const knex = require('knex');

exports.up = async function (knex) {

    await knex.schema.createTable('settings', (table) => {
        table.increments('id').primary(); // Auto-incrementing ID
        table.bigInteger('user_id');
        table.string('key');
        table.string('value');
    });

};

exports.down = async function (knex) {
    // Drop the `users` table
    await knex.schema.dropTableIfExists('settings');
};

 

