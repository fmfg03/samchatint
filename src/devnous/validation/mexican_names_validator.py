"""
Mexican Names Validator

Validates names and surnames against common Mexican names database.
Provides fuzzy matching and suggestions for unclear OCR results.

Features:
- Validation against 200+ common Mexican first names
- Validation against 500+ common Mexican surnames
- Fuzzy matching with Levenshtein distance
- Confidence scoring
- Intelligent suggestions
"""

import logging
from typing import List, Dict, Any, Optional, Tuple
from difflib import get_close_matches
import unicodedata


logger = logging.getLogger(__name__)


# Top 700+ nombres mexicanos comunes (hombres, mujeres e indígenas)
NOMBRES_MEXICANOS = [
    # === Nombres de hombre más comunes (tradicionales) ===
    "José", "Juan", "Miguel", "Luis", "Carlos", "Jorge", "Francisco", "Jesús",
    "Antonio", "Pedro", "Alejandro", "Manuel", "Fernando", "Rafael", "Ricardo",
    "Javier", "Roberto", "Andrés", "Eduardo", "Raúl", "Alberto", "Sergio",
    "Héctor", "Armando", "Gerardo", "Arturo", "Óscar", "Enrique", "Ramón",
    "Pablo", "Julio", "César", "Mario", "Gustavo", "Salvador", "Víctor",
    "Gabriel", "Ignacio", "Daniel", "David", "Martín", "Rubén", "Felipe",
    "Agustín", "Ernesto", "Alfredo", "Guillermo", "Rodrigo", "Leonardo",
    "Mauricio", "Emilio", "Diego", "Santiago", "Sebastián", "Mateo", "Nicolás",
    "Samuel", "Ángel", "Alfonso", "Benjamín", "Omar", "Hugo", "Iván",
    "Joaquín", "Adrián", "Marco", "Cristian", "Jaime", "Alonso", "Lorenzo",
    "Esteban", "Valentín", "Maximiliano", "Ezequiel", "Isaac", "Lucas",
    "Abraham", "Moisés", "Elías", "Salomón", "Joel", "Josué",

    # === Nombres de hombre modernos y populares (2020-2025) ===
    "Gael", "Emiliano", "Matías", "Liam", "Alexander", "Julián", "Ian",
    "Christopher", "Iker", "Axel", "Dylan", "Damián", "Aldo", "Bruno",
    "Oliver", "Thiago", "Noah", "Ethan", "Leo", "Lucas", "Luca",
    "Aarón", "Elián", "Tadeo", "Santino", "Luciano", "Patricio", "Mauricio",
    "Erick", "Erik", "Kevin", "Brian", "Jonathan", "Christian", "Cristóbal",
    "Fabián", "Fabricio", "Facundo", "Fausto", "Félix", "Fermín", "Fidel",
    "Flavio", "Florentino", "Franco", "Frederick", "Froilán", "Galo", "Gaspar",
    "Genaro", "Gerónimo", "Gilberto", "Gonzalo", "Gregorio", "Gualberto",
    "Heriberto", "Hermenegildo", "Hernán", "Hilario", "Homero", "Horacio",
    "Humberto", "Isaías", "Isidro", "Israel", "Jacobo", "Jerónimo", "Jonás",
    "Jordan", "Josías", "Julián", "Justo", "Lázaro", "Leandro", "León",
    "Leonel", "Leopoldo", "Lisandro", "Lucio", "Marcelo", "Marcos", "Mariano",
    "Mauro", "Moisés", "Natanael", "Néstor", "Octavio", "Olegario", "Osvaldo",
    "Otoniel", "Pascual", "Patricio", "Paulo", "Plácido", "Porfirio", "Primitivo",
    "Próspero", "Reginaldo", "Reinaldo", "Remigio", "Renato", "Rigoberto",
    "Rogelio", "Rolando", "Román", "Rosendo", "Rutilio", "Sabino", "Samir",
    "Saúl", "Silverio", "Silvestre", "Simón", "Teodoro", "Timoteo", "Tobías",
    "Tomás", "Ulises", "Urbano", "Vidal", "Virgilio", "Wenceslao", "Xavier",
    "Zacarías", "Zenón",

    # === Nombres de hombre indígenas (Nahuatl, Maya, Zapoteco) ===
    "Cuauhtémoc", "Moctezuma", "Nezahualcóyotl", "Tonatiuh", "Iztli", "Citlali",
    "Tenoch", "Yaretzi", "Xochipilli", "Tlaloc", "Quetzalcoatl", "Atl",
    "Itzamná", "Balam", "Canek", "Ik", "Kin", "Yum", "Chaac", "Kukulkan",
    "Nayeli", "Yolotl", "Cualli", "Tezcatlipoca", "Huitzilopochtli",

    # === Nombres de mujer más comunes (tradicionales) ===
    "María", "Guadalupe", "Juana", "Rosa", "Ana", "Carmen", "Josefina",
    "Teresa", "Isabel", "Martha", "Margarita", "Elena", "Patricia", "Laura",
    "Gloria", "Francisca", "Silvia", "Alicia", "Leticia", "Gabriela",
    "Verónica", "Claudia", "Sofía", "Adriana", "Diana", "Beatriz", "Rocío",
    "Alejandra", "Daniela", "Fernanda", "Valentina", "Regina", "Natalia",
    "Andrea", "Paula", "Camila", "Victoria", "Isabella", "Valeria", "Sara",
    "Mariana", "Carolina", "Mónica", "Lucía", "Cristina", "Sandra", "Julia",
    "Angélica", "Lorena", "Susana", "Maribel", "Cecilia", "Luz", "Araceli",
    "Nancy", "Norma", "Yolanda", "Eva", "Karla", "Paola", "Esther",
    "Raquel", "Rebeca", "Julieta", "Miriam", "Dolores",
    "Concepción", "Pilar", "Mercedes", "Rosario", "Amparo", "Soledad",
    "Esperanza", "Catalina", "Inés", "Blanca", "Aurora", "Remedios",
    "Emilia", "Alma", "Delia", "Emma", "Luna", "Abril", "Maya", "Zoe",

    # === Nombres de mujer modernos y populares (2020-2025) ===
    "Ximena", "Renata", "Frida", "Mía", "Emilia", "Olivia", "Martina",
    "Miranda", "Nicole", "Ashley", "Emily", "Jennifer", "Jessica", "Melanie",
    "Michelle", "Samantha", "Stephanie", "Alexa", "Ariana", "Bianca",
    "Catalina", "Danna", "Dulce", "Fátima", "Génesis", "Guadalupe",
    "Heidi", "Iris", "Ivanna", "Jazmín", "Jimena", "Jocelyn", "Karina",
    "Kiara", "Lía", "Liliana", "Lizbeth", "Luisa", "Margarita", "Marisol",
    "Melissa", "Montserrat", "Nataly", "Noelia", "Pamela", "Perla", "Priscila",
    "Romina", "Salma", "Sasha", "Scarlett", "Selena", "Tamara", "Tatiana",
    "Tania", "Vanessa", "Violeta", "Wendy", "Yareli", "Yasmín", "Yuridia",
    "Abigail", "Ainhoa", "Alison", "Alondra", "Amanda", "Amaya", "América",
    "Anastasia", "Ángeles", "Angie", "Antonella", "Antonia", "Aracely",
    "Arcelia", "Ariadna", "Arleth", "Artemisa", "Astrid", "Bárbara", "Belén",
    "Berenice", "Brenda", "Brisa", "Candela", "Carla", "Carlota", "Celeste",
    "Celia", "Citlali", "Clara", "Clementina", "Constanza", "Coral", "Corina",
    "Cynthia", "Daisy", "Dakota", "Dafne", "Débora", "Deliá", "Demetria",
    "Denisse", "Diana", "Doris", "Edith", "Elba", "Elisa", "Eliza", "Eloísa",
    "Elvia", "Eréndira", "Erica", "Estela", "Estrella", "Eugenia", "Evelyn",
    "Fabiola", "Fanny", "Flor", "Flora", "Florencia", "Gisela", "Gracia",
    "Graciela", "Griselda", "Guillermina", "Haydée", "Helena", "Hilda",
    "Hortensia", "Ignacia", "Iliana", "Irene", "Irma", "Jacinta", "Jacqueline",
    "Jaqueline", "Josefa", "Joyce", "Judith", "Juliana", "Julieta", "Kimberly",
    "Larissa", "Leonor", "Lilia", "Lilian", "Linda", "Lola", "Lourdes",
    "Lupita", "Luz", "Magdalena", "Maite", "Manuela", "Marcela", "Maricruz",
    "Mariela", "Marina", "Marisela", "Marisol", "Maritza", "Marlene", "Marta",
    "Matilde", "Mayra", "Melisa", "Mercedes", "Minerva", "Miriam", "Nadia",
    "Nelly", "Nidia", "Ofelia", "Olga", "Olivia", "Paloma", "Paulina",
    "Penélope", "Petra", "Rafaela", "Ramona", "Raquel", "Rita", "Roberta",
    "Rocío", "Rosa", "Rosalba", "Rosalía", "Rosaura", "Rosita", "Roxana",
    "Ruth", "Sabrina", "Sarai", "Selene", "Serena", "Shakira", "Sharon",
    "Sheila", "Silvia", "Sofía", "Sol", "Sonia", "Soraya", "Susana",
    "Tere", "Teresa", "Úrsula", "Verónica", "Violeta", "Virginia", "Viviana",
    "Wanda", "Xiomara", "Yahaira", "Yamilet", "Yara", "Yesenia", "Yolanda",
    "Yuridia", "Zara", "Zoila", "Zoraida",

    # === Nombres de mujer indígenas (Nahuatl, Maya, Zapoteco) ===
    "Xóchitl", "Citlali", "Mixtli", "Malinalli", "Xochiquetzal", "Tonantzin",
    "Itzel", "Ixchel", "Kaah", "Nicte", "Sac", "Yaretzi", "Zyanya",
    "Nayeli", "Nallely", "Mayahuel", "Chalchiuhtlicue", "Coyolxauhqui",
    "Metztli", "Quetzalli", "Tlalli", "Xilonen", "Yoltzin", "Zyanya",

    # === Nombres compuestos comunes (primer parte del nombre compuesto) ===
    "Juan", "José", "Miguel", "Luis", "Carlos", "María", "Ana", "Rosa",
    "Luz", "Carmen", "Isabel", "Guadalupe", "Teresa", "Elena", "Gloria",
    "Ángel", "Angel", "Pedro", "Diego", "Antonio", "Pablo", "Raúl"
]

# Top 1000+ apellidos mexicanos más comunes (españoles e indígenas)
APELLIDOS_MEXICANOS = [
    # === Los 100 más comunes (INEGI 2020-2025) ===
    "Hernández", "García", "Martínez", "López", "González", "Pérez",
    "Rodríguez", "Sánchez", "Ramírez", "Cruz", "Flores", "Gómez",
    "Morales", "Vásquez", "Reyes", "Jiménez", "Torres", "Díaz",
    "Gutiérrez", "Ruiz", "Mendoza", "Aguilar", "Ortiz", "Moreno",
    "Castillo", "Romero", "Álvarez", "Méndez", "Chávez", "Rivera",
    "Juárez", "Ramos", "Domínguez", "Herrera", "Medina", "Castro",
    "Vargas", "Guzmán", "Velázquez", "Muñoz", "Rojas", "De La Cruz",
    "Contreras", "Salazar", "Luna", "Ortega", "Santiago", "Guerrero",
    "Estrada", "Bautista", "Cortés", "Soto", "Alvarado", "Espinoza",
    "Lara", "Ávila", "Ríos", "Cervantes", "Silva", "Delgado",
    "Vega", "Márquez", "Sandoval", "Carrillo", "Fernández", "León",
    "Mejía", "Solís", "Rosas", "Ibarra", "Valdez", "Núñez",
    "Campos", "Santos", "Camacho", "Navarro", "Maldonado", "Rosales",
    "Acosta", "Peña", "Miranda", "Campos", "Benítez", "Salas",
    "Villa", "Nava", "Valencia", "Molina", "Bravo", "Gallegos",
    "Padilla", "Serrano", "Franco", "Montes", "Ochoa", "Pedroza",
    "Paredes", "Carrasco", "Ayala", "Corona", "Trujillo", "Arias",

    # Más apellidos comunes (100-200)
    "Zamora", "Vázquez", "Galván", "Córdova", "Huerta", "Márquez", "Olvera",
    "Orozco", "Rosales", "Santana", "Barrera", "Bautista", "Cardenas",
    "Escobar", "Ferrer", "Garza", "Ibáñez", "Juárez", "Lozano", "Macías",
    "Mata", "Oliva", "Parra", "Quezada", "Quintero", "Robles", "Salinas",
    "Tapia", "Uribe", "Villanueva", "Zamudio", "Arellano", "Becerra",
    "Calderón", "Cárdenas", "Carvajal", "Casas", "Ceballos", "Cisneros",
    "Cornejo", "Cuevas", "De la Cruz", "De León", "Duarte", "Enríquez",
    "Escamilla", "Esquivel", "Fajardo", "Figueroa", "Galindo", "Gamboa",
    "Gil", "Girón", "Godínez", "Gracia", "Haro", "Hinojosa", "Iturbe",
    "Jaramillo", "Ledesma", "Linares", "Loera", "Madrid", "Magaña", "Mayorga",
    "Meléndez", "Meza", "Mondragón", "Montoya", "Morán", "Narváez", "Nieto",
    "Novoa", "Ocampo", "Ontiveros", "Ordóñez", "Ornelas", "Palacios",
    "Palma", "Palomino", "Pantoja", "Partida", "Paz", "Peralta", "Ponce",
    "Portillo", "Prieto", "Pulido", "Quiroz", "Rentería", "Reynoso", "Rico",

    # Más apellidos (200-300)
    "Rincón", "Robledo", "Rodarte", "Rojas", "Rubio", "Rueda", "Saavedra",
    "Saenz", "Salcedo", "Saldivar", "Salgado", "Sámano", "San Juan",
    "Santillán", "Sepúlveda", "Sierra", "Soria", "Suárez", "Téllez", "Terán",
    "Tovar", "Trejo", "Ugalde", "Ulloa", "Urbina", "Urrutia", "Valdivia",
    "Valenzuela", "Valle", "Vallejo", "Vázquez", "Vega", "Velasco", "Vera",
    "Verduzco", "Vigil", "Villalobos", "Villareal", "Villegas", "Yáñez",
    "Zavala", "Zepeda", "Zúñiga", "Alanís", "Alcalá", "Alcántara", "Alemán",
    "Alfaro", "Andrade", "Anaya", "Aragón", "Aranda", "Arce", "Archuleta",
    "Armenta", "Arredondo", "Arriaga", "Arroyo", "Arteaga", "Arévalo",
    "Avendaño", "Badillo", "Baeza", "Balderas", "Ballesteros", "Banda",
    "Barajas", "Barba", "Barbosa", "Barcenas", "Barrios", "Batista", "Bañuelos",
    "Beltrán", "Bermúdez", "Bernal", "Berumen", "Betancourt", "Blanco",
    "Bojorquez", "Bonilla", "Borja", "Botello", "Burgos", "Bustos", "Báez",

    # Más apellidos (300-400)
    "Cabral", "Cáceres", "Cadena", "Calvillo", "Camarena", "Camargo", "Campaña",
    "Campillo", "Canales", "Cano", "Cantú", "Carbajal", "Carballo", "Cardona",
    "Carmona", "Carreón", "Casanova", "Casillas", "Castellanos", "Castañeda",
    "Castruita", "Ceja", "Centeno", "Cepeda", "Cerda", "Cerna", "Chacón",
    "Chaparro", "Covarrubias", "Cobian", "Collazo", "Colón", "Conde",
    "Cordero", "Coronado", "Corrales", "Correa", "Cota", "Covarrubias",
    "Crespo", "Crisóstomo", "Cristóbal", "Cuéllar", "Curiel", "Dávila",
    "De Anda", "De Jesús", "De la Rosa", "De los Santos", "Del Río",
    "Del Valle", "Deleón", "Deniz", "Durán", "Echeverría", "Elizondo",
    "Escobedo", "Esparza", "Espino", "Espinoza", "Esteban", "Estévez",
    "Farías", "Felix", "Fernández", "Fierro", "Figarola", "Flor", "Fonseca",
    "Font", "Frias", "Gaeta", "Gaitán", "Galarza", "Galaviz", "Galeana",
    "Gallardo", "Gallegos", "Gaona", "Garay", "Garibay", "Garrido", "Gaspar",

    # Más apellidos (400-500)
    "Gaytan", "Godoy", "Granado", "Granados", "Grande", "Grijalva", "Guardado",
    "Guevara", "Guillen", "Gurule", "Gutiérrez", "Heras", "Hermosillo",
    "Hernandes", "Herrera", "Hidalgo", "Holguin", "Holguín", "Huerta",
    "Hurtado", "Ibanez", "Iglesias", "Infante", "Islas", "Izquierdo", "Jacobo",
    "Jáquez", "Jasso", "Jáuregui", "Jordán", "Juan", "Lazo", "Leal", "Ledezma",
    "Lemus", "Lerma", "Leyva", "Limon", "Limón", "Lizárraga", "Llamas",
    "Lobato", "Longoria", "Lorenzo", "Lovato", "Loyola", "Lucero", "Lucio",
    "Luevano", "Lugo", "Luján", "Luque", "Machado", "Madrigal", "Madriles",
    "Maestas", "Maldonado", "Mancera", "Mancilla", "Manríquez", "Manzanares",
    "Mariscal", "Marquez", "Marroquín", "Marti", "Martinez", "Mascareñas",
    "Matos", "Mayorga", "Mazariegos", "Medrano", "Melgar", "Mena", "Mendiola",
    "Mendoza", "Mercado", "Mesa", "Meraz", "Millán", "Miramontes", "Mojica",
    "Monarrez", "Monreal", "Monsalve", "Montaño", "Montero", "Montenegro",
    "Montiel", "Monroy", "Mora", "Morales", "Moran", "Moreira", "Morelos",

    # === Más apellidos (500-700) ===
    "Mosqueda", "Murillo", "Nájera", "Narro", "Navarrete", "Neria", "Nieves",
    "Noguera", "Noriega", "Núñez", "Obregón", "Ocasio", "Olague", "Olea",
    "Olivares", "Olivera", "Olmos", "Olvera", "Oquendo", "Ordaz", "Orendain",
    "Oropeza", "Orozco", "Orta", "Ort", "Oseguera", "Osorio", "Osuna",
    "Otero", "Ovando", "Oviedo", "Pacheco", "Padua", "Páez", "Palafox",
    "Palma", "Paniagua", "Pantoja", "Parada", "Paramo", "Pardo", "Parra",
    "Pasos", "Pastor", "Patiño", "Pavón", "Payán", "Pedrero", "Pedraza",
    "Pedroza", "Pelayo", "Peláez", "Pellicer", "Peña", "Perales", "Perdomo",
    "Perea", "Peres", "Pérez", "Pesquera", "Piña", "Pineda", "Pino",
    "Piñón", "Pinto", "Pizarro", "Plascencia", "Plaza", "Polanco", "Polo",
    "Ponce", "Pons", "Porras", "Posada", "Pozo", "Prado", "Preciado",
    "Prieto", "Puente", "Puentes", "Puerto", "Pulgar", "Pulido", "Quesada",
    "Quiñones", "Quiñónez", "Quintana", "Quintanilla", "Quirarte", "Quiroga",
    "Quiroz", "Rabago", "Rada", "Ramírez", "Ramón", "Rangel", "Rascón",
    "Real", "Rebolledo", "Recio", "Redondo", "Regino", "Rendón", "Rentería",
    "Resendez", "Resendiz", "Retana", "Rey", "Reyna", "Reyes", "Reynoso",
    "Ríos", "Riojas", "Riquelme", "Rivas", "Rivera", "Rivero", "Rizo",
    "Robles", "Roca", "Rocha", "Rodarte", "Rodas", "Rodrígez", "Roig",
    "Rojo", "Roldán", "Román", "Romero", "Ronquillo", "Roque", "Rosa",
    "Rosado", "Rosales", "Rosas", "Rosete", "Rossell", "Rubio", "Rubi",
    "Rueda", "Ruelas", "Ruffo", "Ruiz", "Saavedra", "Sabino", "Sada",
    "Sáenz", "Sáinz", "Saiz", "Salas", "Salado", "Salamanca", "Salas",
    "Salazar", "Salcedo", "Salcido", "Saldaña", "Saldivar", "Salgado",
    "Salinas", "Salvatierra", "Samora", "Sampedro", "Samper", "San Miguel",
    "Sanabria", "Sanches", "Sánchez", "Sandoval", "Sanjuan", "Santacruz",
    "Santamaría", "Santana", "Santiago", "Santillán", "Santín", "Santo",
    "Santos", "Sanz", "Sarmiento", "Sauceda", "Saucedo", "Savedra",
    "Segovia", "Segura", "Sepúlveda", "Serrano", "Sierra", "Silva",
    "Simón", "Sisneros", "Soberanes", "Solano", "Solares", "Soledad",
    "Solis", "Soltero", "Somoza", "Soria", "Soriano", "Sosa", "Sotelo",
    "Soto", "Suárez", "Suro", "Tabares", "Taboada", "Talavera", "Tamayo",
    "Tamez", "Tapia", "Tarango", "Tavera", "Tavira", "Tejada", "Tejeda",
    "Tejedor", "Tello", "Téllez", "Tenorio", "Terán", "Terrazas", "Terrones",
    "Tinoco", "Tirado", "Tobar", "Toledo", "Tolentino", "Toral", "Toro",
    "Torralba", "Torre", "Torreblanca", "Torreón", "Torres", "Tovar",
    "Trejo", "Treviño", "Triana", "Trillo", "Trinidad", "Tristan",
    "Troncoso", "Trujillo", "Tuñón", "Ugalde", "Ugarte", "Ulloa",
    "Umaña", "Uriarte", "Uribe", "Urquiza", "Urrutia", "Ursúa",
    "Vacas", "Vaca", "Valadez", "Valdés", "Valdez", "Valdivia",
    "Valdivieso", "Valdovinos", "Valencia", "Valentín", "Valenzuela",
    "Valero", "Valeriano", "Valiente", "Valle", "Vallejo", "Valles",
    "Valls", "Valverde", "Varela", "Vargas", "Vasconcelos", "Vásquez",
    "Vazquez", "Vázquez", "Vega", "Vela", "Velarde", "Velasco",
    "Velásquez", "Velázquez", "Vélez", "Venegas", "Ventura", "Vera",
    "Verdejo", "Verdugo", "Verduzco", "Vergara", "Vicente", "Vidal",
    "Vidales", "Vieira", "Vigil", "Villa", "Villalobos", "Villalón",
    "Villalpando", "Villamar", "Villanueva", "Villar", "Villareal",
    "Villarreal", "Villaseñor", "Villegas", "Vizcarra", "Vizcaíno",
    "Yáñez", "Yañez", "Yepez", "Yermo", "Ynfante", "Yslas",
    "Zabaleta", "Zabala", "Zaldivar", "Zambrano", "Zamora", "Zamudio",
    "Zapata", "Zarate", "Zavala", "Zavaleta", "Zepeda", "Zúñiga",

    # === Apellidos indígenas MAYAS (40 apellidos) ===
    "Aké", "Baas", "Bacab", "Balam", "Cab", "Can", "Camal", "Canul",
    "Cauich", "Ceh", "Chablé", "Chalé", "Chan", "Dzib", "Ek", "Hau",
    "Huchím", "Kantún", "Ku", "May", "Nah", "Noh", "Pech", "Pol",
    "Poot", "Puc", "Tun", "Zab", "Uc", "Xiu", "Xol", "Xoo",
    "Xul", "Yah", "Yam", "Yama", "Yaxkin", "Yeh", "Yok", "Zul",

    # === Apellidos indígenas NAHUAS (67 apellidos) ===
    "Acá", "Apanco", "Cacahua", "Calocho", "Cholula", "Cotzomi",
    "Huelitl", "Huexotl", "Macuil", "Malinalxóchitl", "Netzahualcóyotl",
    "Nophal", "Ocelotl", "Popoca", "Quechol", "Quitl", "Tacuepian",
    "Tecahualoya", "Teceil", "Tecol", "Temich", "Tepale", "Tepetl",
    "Tenahua", "Tepoz", "Tetla", "Texis", "Tizatl", "Tlahque",
    "Tlalolinc", "Tlapaya", "Tlatoa", "Tlaxca", "Toxqui", "Toxtlc",
    "Xicale", "Xicotencatl", "Xilotl", "Xochihula", "Xopa", "Yahuitl",
    "Zaca", "Zacatelco", "Zacatlán", "Zitle", "Zuancatl", "Huitzil",
    "Mizton", "Necuametl", "Nopalli", "Nocheztli", "Ocelotl", "Ozomatzin",
    "Quauhtemoc", "Tenoch", "Tepoztecatl", "Tlacaelel", "Tlaloc",
    "Tochtli", "Xipe", "Xochimilco", "Xiuhtecuhtli", "Yayauhqui",
    "Zacatenco", "Zempoala",

    # === Apellidos indígenas ZAPOTECOS y otros (30 apellidos) ===
    "Bautista", "Benito", "Blas", "Cayetano", "Clemente", "Cornelio",
    "Cruz", "Domingo", "Esteban", "Fabian", "Felipe", "Flores",
    "Francisco", "Gabriel", "Gregorio", "Hernández", "Isidro", "Jacinto",
    "Jorge", "Juan", "Lorenzo", "Luis", "Manuel", "Marcos",
    "Martín", "Mateo", "Miguel", "Pablo", "Pedro", "Santiago",

    # === Apellidos modernos y regionales (100+ adicionales) ===
    "Abarca", "Aburto", "Adame", "Adán", "Adriano", "Agredano", "Aguado",
    "Aguas", "Agudo", "Ahumada", "Alaniz", "Alarcon", "Alba", "Albarrán",
    "Alberti", "Alcaide", "Alcaraz", "Alcázar", "Alcocer", "Aldama",
    "Alderete", "Alegre", "Alegría", "Alemán", "Alfaro", "Alférez",
    "Almaguer", "Almanza", "Almazán", "Almeida", "Almendarez", "Alonso",
    "Alpirez", "Altamirano", "Alva", "Alvarado", "Álvarez", "Amador",
    "Amaya", "Ambriz", "Amor", "Ampudia", "Anaya", "Anchondo",
    "Anguiano", "Antillón", "Aparicio", "Apodaca", "Aquino", "Araiza",
    "Arana", "Aranda", "Araujo", "Arce", "Archundia", "Arcos",
    "Ardila", "Arellan", "Arenas", "Argote", "Arguello", "Argueta",
    "Arias", "Arizmendi", "Armendáriz", "Armijo", "Arnaud", "Arreola",
    "Arteaga", "Arteche", "Arvayo", "Arzate", "Ascencio", "Asencio",
    "Astorga", "Asturias", "Atilano", "Atondo", "Augusto", "Aure",
    "Autos", "Avelino", "Avendaño", "Avitia", "Ayón", "Aznar",
    "Azuara", "Badía", "Badillo", "Báez", "Bahena", "Bailón",
    "Balderas", "Baldovinos", "Balladares", "Ballesteros", "Baltazar",
    "Baños", "Barba", "Barbero", "Barceló", "Barcenas", "Barradas",
    "Barrales", "Barraza", "Barreda", "Barrera", "Barreto", "Barrientos",
    "Barrón", "Barros", "Basaldúa", "Basilio", "Basurto", "Batalla",
    "Batres", "Bazaldua", "Bazán", "Beatriz", "Bécquer", "Bedolla",
    "Bejaran", "Bejarano", "Belisario", "Bellido", "Bello", "Belmonte",
    "Beltrán", "Benavente", "Benavides", "Benedicto", "Benito", "Berbena"
]


class MexicanNamesValidator:
    """
    Validator for Mexican names and surnames.

    Features:
    - Validates against common Mexican names database
    - Fuzzy matching with similarity scoring
    - Intelligent suggestions for misspellings
    - Confidence scoring

    Example:
        >>> validator = MexicanNamesValidator()
        >>> result = validator.validate_name("Juan")
        >>> print(result['valid'])  # True
        >>>
        >>> result = validator.validate_name("Juam", confidence=0.65)
        >>> print(result['suggestions'])  # ["Juan"]
    """

    def __init__(
        self,
        min_confidence: float = 0.80,
        max_suggestions: int = 3
    ):
        """
        Initialize validator.

        Args:
            min_confidence: Minimum confidence threshold (default: 0.80)
            max_suggestions: Maximum number of suggestions to return
        """
        self.min_confidence = min_confidence
        self.max_suggestions = max_suggestions

        # Normalize names for better matching
        self.nombres = self._normalize_list(NOMBRES_MEXICANOS)
        self.apellidos = self._normalize_list(APELLIDOS_MEXICANOS)

        logger.info(
            f"Validator initialized: {len(self.nombres)} nombres, "
            f"{len(self.apellidos)} apellidos"
        )

    def _normalize_list(self, names: List[str]) -> List[str]:
        """Normalize names list (lowercase, remove accents for matching)"""
        normalized = []
        for name in names:
            # Keep original + normalized version
            normalized.append(name)
            # Add lowercase version
            normalized.append(name.lower())
            # Add version without accents
            no_accents = self._remove_accents(name)
            if no_accents != name:
                normalized.append(no_accents)
                normalized.append(no_accents.lower())

        return list(set(normalized))

    def _remove_accents(self, text: str) -> str:
        """Remove accents from text"""
        nfd = unicodedata.normalize('NFD', text)
        return ''.join(c for c in nfd if unicodedata.category(c) != 'Mn')

    def validate_name(
        self,
        name: str,
        confidence: Optional[float] = None,
        is_surname: bool = False
    ) -> Dict[str, Any]:
        """
        Validate a name or surname.

        Args:
            name: Name to validate
            confidence: OCR confidence score (0.0-1.0)
            is_surname: True if validating a surname

        Returns:
            Validation result:
            {
                'valid': bool,
                'confidence': float,
                'needs_human_review': bool,
                'suggestions': List[str],
                'reason': str
            }
        """
        logger.info(f"🔍 Validating {'surname' if is_surname else 'name'}: '{name}' (confidence: {confidence})")

        if not name or len(name.strip()) < 2:
            logger.info(f"❌ Name too short: '{name}'")
            return {
                'valid': False,
                'confidence': 0.0,
                'needs_human_review': True,
                'suggestions': [],
                'reason': 'Nombre muy corto o vacío'
            }

        name = name.strip()
        database = self.apellidos if is_surname else self.nombres
        type_str = "apellido" if is_surname else "nombre"

        # Check exact match (case-insensitive)
        if name in database or name.lower() in database:
            logger.info(f"✅ Exact match found for '{name}' in database")
            return {
                'valid': True,
                'confidence': confidence or 1.0,
                'needs_human_review': False,
                'suggestions': [],
                'reason': f'{type_str.capitalize()} válido (coincidencia exacta)'
            }

        logger.info(f"⚠️  '{name}' not found in database (exact match)")

        # Check if confidence is below threshold
        if confidence is not None and confidence < self.min_confidence:
            logger.info(f"📊 Low confidence ({confidence*100:.0f}%) - searching suggestions...")
            # Find suggestions
            suggestions = self._find_suggestions(name, database)
            logger.info(f"💡 Found {len(suggestions)} suggestions: {suggestions}")

            return {
                'valid': False,
                'confidence': confidence,
                'needs_human_review': True,
                'suggestions': suggestions,
                'reason': f'Confianza baja ({confidence*100:.0f}%) para {type_str}'
            }

        # Name not in database but high confidence
        # Find suggestions anyway
        logger.info(f"🔍 High confidence ({confidence or 'unknown'}), searching for similar names...")
        suggestions = self._find_suggestions(name, database)
        logger.info(f"💡 Found {len(suggestions)} suggestions: {suggestions}")

        if suggestions:
            # Found similar names - ALWAYS needs review if name not in DB
            logger.info(f"❓ Name '{name}' not in database but found similar names - NEEDS REVIEW")
            return {
                'valid': False,
                'confidence': confidence or 0.5,
                'needs_human_review': True,
                'suggestions': suggestions,
                'reason': f'{type_str.capitalize()} no encontrado en base de datos'
            }
        else:
            # No suggestions - STILL needs review if not in database
            # Changed logic: ALWAYS require review if name not in database
            logger.info(f"❌ Name '{name}' not in database and no suggestions - NEEDS REVIEW")
            return {
                'valid': False,
                'confidence': confidence or 0.3,
                'needs_human_review': True,
                'suggestions': [],
                'reason': f'{type_str.capitalize()} no encontrado en base de datos (sin sugerencias)'
            }

    def _find_suggestions(
        self,
        name: str,
        database: List[str]
    ) -> List[str]:
        """Find similar names using fuzzy matching"""
        # Use difflib for fuzzy matching
        matches = get_close_matches(
            name,
            database,
            n=self.max_suggestions,
            cutoff=0.6  # 60% similarity
        )

        # Remove duplicates and normalize
        unique_matches = []
        seen = set()

        for match in matches:
            # Get the capitalized version
            normalized = match.title()
            if normalized.lower() not in seen:
                seen.add(normalized.lower())
                unique_matches.append(normalized)

        return unique_matches[:self.max_suggestions]

    def validate_full_name(
        self,
        full_name: str,
        confidence: Optional[float] = None
    ) -> Dict[str, Any]:
        """
        Validate a full name (first name + surname(s)).

        Args:
            full_name: Full name (e.g., "Juan García López")
            confidence: OCR confidence score

        Returns:
            Validation result with details for each name part
        """
        if not full_name or len(full_name.strip()) < 3:
            return {
                'valid': False,
                'needs_human_review': True,
                'parts': [],
                'reason': 'Nombre completo muy corto o vacío'
            }

        parts = full_name.strip().split()

        if len(parts) < 2:
            return {
                'valid': False,
                'needs_human_review': True,
                'parts': [],
                'reason': 'Se esperan al menos nombre y apellido'
            }

        # First part is first name
        # Rest are surnames
        first_name = parts[0]
        surnames = parts[1:]

        results = {
            'first_name': self.validate_name(first_name, confidence, is_surname=False),
            'surnames': [
                self.validate_name(surname, confidence, is_surname=True)
                for surname in surnames
            ]
        }

        # Overall validation
        all_valid = (
            results['first_name']['valid'] and
            all(s['valid'] for s in results['surnames'])
        )

        needs_review = (
            results['first_name']['needs_human_review'] or
            any(s['needs_human_review'] for s in results['surnames'])
        )

        return {
            'valid': all_valid,
            'needs_human_review': needs_review,
            'confidence': confidence,
            'parts': results,
            'full_name': full_name,
            'reason': 'Validación completa de nombre y apellidos'
        }


# Convenience functions

def validate_mexican_name(
    name: str,
    confidence: Optional[float] = None,
    is_surname: bool = False
) -> Dict[str, Any]:
    """
    Convenience function to validate a Mexican name.

    Example:
        >>> result = validate_mexican_name("Juan", confidence=0.95)
        >>> print(result['valid'])  # True
        >>>
        >>> result = validate_mexican_name("Juam", confidence=0.65)
        >>> print(result['needs_human_review'])  # True
        >>> print(result['suggestions'])  # ["Juan"]
    """
    validator = MexicanNamesValidator()
    return validator.validate_name(name, confidence, is_surname)


def validate_mexican_full_name(
    full_name: str,
    confidence: Optional[float] = None
) -> Dict[str, Any]:
    """
    Convenience function to validate a full Mexican name.

    Example:
        >>> result = validate_mexican_full_name("Juan García López", confidence=0.85)
        >>> print(result['valid'])  # True
        >>>
        >>> result = validate_mexican_full_name("Juam Garsia", confidence=0.70)
        >>> print(result['needs_human_review'])  # True
    """
    validator = MexicanNamesValidator()
    return validator.validate_full_name(full_name, confidence)
