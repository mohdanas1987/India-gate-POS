const knex = require('knex');

exports.up = async function (knex) {
    await knex.schema.dropTableIfExists('reports');
    await knex.schema.createTable('reports', (table) => {
        table.increments('id').primary(); // Auto-incrementing ID
        table.string('path');
        table.date('date');
    });

};

exports.down = async function (knex) {
    // Drop the `reports` table
    await knex.schema.dropTableIfExists('reports');
};


