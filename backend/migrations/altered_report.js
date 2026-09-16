exports.up = async function (knex) {
    await knex.schema.dropTableIfExists('reports');
    await knex.schema.createTable('reports', (table) => {
        table.increments('id').primary(); // Auto-incrementing ID
        table.string('path');
        table.timestamps(true);
    });

};

exports.down = async function (knex) {
    // Drop the `reports` table
    await knex.schema.dropTableIfExists('reports');
};


