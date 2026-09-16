exports.up = async function (knex) {
    await knex.schema.table('products', table => table.boolean('deleted').defaultTo(false));
};

exports.down = async function (knex) {
    await knex.schema.table('products', table => table.dropColumn('deleted'));
};

 