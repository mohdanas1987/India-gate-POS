const knex = require('knex');

exports.up = async function (knex) {

    await knex.schema.createTable('reports', (table) => {
        table.increments('id').primary(); // Auto-incrementing ID
        table.longText('data');
        table.timestamps(true,true);
    });

};

exports.down = async function (knex) {
    // Drop the `users` table
    await knex.schema.dropTableIfExists('reports');
};


