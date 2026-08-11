package main

import (
	"log"

	"github.com/gofiber/fiber/v3"
)

func main() {
	// Crear una nueva aplicación Fiber
	app := fiber.New()

	// Ruta raíz - Hola Mundo
	app.Get("/", func(c fiber.Ctx) error {
		return c.SendString("¡Hola Mundo! 🚀")
	})

	// Ruta con parámetro opcional
	app.Get("/saludo/:nombre", func(c fiber.Ctx) error {
		nombre := c.Params("nombre")
		return c.SendString("¡Hola " + nombre + " desde Fiber! 👋")
	})

	// Ruta JSON de ejemplo
	app.Get("/api", func(c fiber.Ctx) error {
		return c.JSON(fiber.Map{
			"mensaje": "¡Hola Mundo!",
			"framework": "Fiber",
			"lenguaje": "Go",
		})
	})

	// Iniciar el servidor en el puerto 3000
	log.Fatal(app.Listen(":3000"))
}
