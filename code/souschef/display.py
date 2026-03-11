"""Rich terminal formatting for souschef output."""

from __future__ import annotations

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from souschef.models import GroceryList, Meal, MealPlan, MealWeek, PantryItem, Recipe
from souschef.planner import DAY_NAMES

console = Console()


def show_meals(meals: list[Meal], start_date: str = "") -> None:
    """Display meals in a Rich table."""
    title = f"Meals — Week of {start_date}" if start_date else "Meals"
    table = Table(title=title, show_lines=True)
    table.add_column("Day", style="bold cyan", width=12)
    table.add_column("Meal", style="white", min_width=30)
    table.add_column("Side", style="dim", min_width=18)
    table.add_column("Status", width=10)

    status_colors = {"accepted": "green", "suggested": "yellow", "rejected": "red"}

    for meal in meals:
        color = status_colors.get(meal.status, "white")
        status_text = f"[{color}]{meal.status}[/{color}]"
        meal_text = meal.recipe_name
        if meal.is_followup:
            meal_text += " [dim](uses leftovers)[/dim]"
        table.add_row(meal.day_name, meal_text, meal.side, status_text)

    console.print(table)


def show_plan(plan: MealPlan) -> None:
    """Legacy: display a MealPlan."""
    table = Table(title=f"Meal Plan — Week of {plan.week_of}", show_lines=True)
    table.add_column("Day", style="bold cyan", width=12)
    table.add_column("Meal", style="white", min_width=30)
    table.add_column("Side", style="dim", min_width=18)
    table.add_column("Status", width=10)

    status_colors = {"accepted": "green", "suggested": "yellow", "rejected": "red"}

    for slot in plan.slots:
        color = status_colors.get(slot.status, "white")
        status_text = f"[{color}]{slot.status}[/{color}]"
        meal_text = slot.recipe_name
        if slot.is_followup:
            meal_text += " [dim](uses leftovers)[/dim]"
        table.add_row(DAY_NAMES[slot.day_of_week], meal_text, slot.side, status_text)

    console.print(table)


def show_recipe(recipe: Recipe) -> None:
    info = (
        f"Cuisine: {recipe.cuisine}  |  Effort: {recipe.effort}  |  Cleanup: {recipe.cleanup}\n"
        f"Prep: {recipe.prep_minutes}min  |  Cook: {recipe.cook_minutes}min  |  Servings: {recipe.servings}"
    )
    tags = []
    if recipe.outdoor:
        tags.append("outdoor")
    if recipe.kid_friendly:
        tags.append("kid-friendly")
    if recipe.premade:
        tags.append("premade")
    if tags:
        info += f"\nTags: {', '.join(tags)}"
    if recipe.notes:
        info += f"\nNotes: {recipe.notes}"

    console.print(Panel(info, title=recipe.name, border_style="cyan"))

    if recipe.ingredients:
        table = Table(show_header=True)
        table.add_column("Ingredient", style="white")
        table.add_column("Qty", justify="right")
        table.add_column("Unit")
        table.add_column("Prep", style="dim")

        for ing in recipe.ingredients:
            qty = f"{ing.quantity:g}"
            table.add_row(ing.ingredient_name, qty, ing.unit, ing.prep_note)

        console.print(table)


def show_recipe_list(recipes: list[Recipe]) -> None:
    table = Table(title="Recipes")
    table.add_column("ID", justify="right", style="dim")
    table.add_column("Name", style="bold")
    table.add_column("Cuisine")
    table.add_column("Effort")
    table.add_column("Time", justify="right")

    for r in recipes:
        total = r.prep_minutes + r.cook_minutes
        table.add_row(str(r.id), r.name, r.cuisine, r.effort, f"{total}min")

    console.print(table)


def show_grocery_list(gl: GroceryList, by_store: dict[str, list]) -> None:
    for store, items in sorted(by_store.items()):
        store_label = {"sams": "Sam's Club", "kroger": "Kroger", "either": "Either Store"}.get(
            store, store
        )
        table = Table(title=store_label, show_lines=False)
        table.add_column("Item", style="white", min_width=20)
        table.add_column("Qty", justify="right")
        table.add_column("Unit")
        table.add_column("Aisle", style="dim")

        for item in items:
            qty = f"{item.total_quantity:g}"
            table.add_row(item.ingredient_name, qty, item.unit, item.aisle)

        console.print(table)
        console.print()

    if gl.staples_used:
        staple_list = ", ".join(sorted(set(gl.staples_used)))
        console.print(
            Panel(
                f"Double-check that you have: [bold]{staple_list}[/bold]",
                title="Pantry Staples",
                border_style="yellow",
            )
        )


def show_pantry(items: list[PantryItem]) -> None:
    if not items:
        console.print("[dim]Pantry is empty.[/dim]")
        return

    table = Table(title="Pantry")
    table.add_column("Ingredient", style="white")
    table.add_column("Qty", justify="right")
    table.add_column("Unit")
    table.add_column("Updated", style="dim")

    for item in items:
        qty = f"{item.quantity:g}"
        table.add_row(item.ingredient_name, qty, item.unit, item.updated_at)

    console.print(table)


def show_bulk_tips(tips: list[str]) -> None:
    if tips:
        console.print(Panel("\n".join(tips), title="Bulk Prep Tips", border_style="green"))
