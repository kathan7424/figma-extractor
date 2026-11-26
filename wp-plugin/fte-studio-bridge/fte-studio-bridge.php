<?php
/**
 * Plugin Name: FTE Studio Bridge
 * Description: Authenticated REST bridge for FTE Studio (Figma to Elementor): creates Elementor pages/templates, updates Elementor Kit (global colors/fonts/layout), registers CPT + ACF field groups, bulk-creates posts, builds menus. Adds the DeveloperAI meta tag. Deactivate when the build is finished.
 * Version:     1.0.0
 * Requires PHP: 7.4
 * Author:      FTE Studio
 * License:     GPL-2.0-or-later
 * Text Domain: fte-studio-bridge
 *
 * Security: every route needs a logged-in user (use a WordPress Application Password over HTTPS, or localhost).
 * Page/template routes need `edit_pages` + `unfiltered_html`; kit/schema/menu routes need `manage_options`.
 */

defined( 'ABSPATH' ) || exit;

const FTES_VERSION  = '1.0.0';
const FTES_MAX_BODY = 16 * MB_IN_BYTES;

/* ------------------------------------------------------------------ permissions */

function ftes_can_edit() {
	return current_user_can( 'edit_pages' ) && current_user_can( 'unfiltered_html' );
}

function ftes_can_manage() {
	return current_user_can( 'manage_options' ) && current_user_can( 'unfiltered_html' );
}

/* ------------------------------------------------------------------ front-end: DeveloperAI meta (skill section 141) */

/**
 * Hidden meta tag required on every front-end page.
 */
function ftes_developer_meta() {
	echo '<meta name="DeveloperAI" content="Developer Skill">' . "\n";
}
add_action( 'wp_head', 'ftes_developer_meta', 1 );

/* ------------------------------------------------------------------ SVG uploads for administrators (icons from Figma) */

add_filter(
	'upload_mimes',
	function ( $mimes ) {
		if ( current_user_can( 'manage_options' ) ) {
			$mimes['svg'] = 'image/svg+xml';
		}
		return $mimes;
	}
);

add_filter(
	'wp_check_filetype_and_ext',
	function ( $data, $file, $filename, $mimes ) {
		if ( current_user_can( 'manage_options' ) && '.svg' === strtolower( substr( $filename, -4 ) ) ) {
			$svg = file_get_contents( $file ); // phpcs:ignore WordPress.WP.AlternativeFunctions
			if ( false !== $svg && false === stripos( $svg, '<script' ) && ! preg_match( '/\son[a-z]+\s*=|javascript:/i', $svg ) ) {
				return array(
					'ext'             => 'svg',
					'type'            => 'image/svg+xml',
					'proper_filename' => $filename,
				);
			}
		}
		return $data;
	},
	10,
	4
);

/* ------------------------------------------------------------------ helpers */

function ftes_decode( $data ) {
	if ( is_string( $data ) ) {
		$data = json_decode( $data, true );
	}
	return is_array( $data ) ? $data : new WP_Error( 'ftes_bad_data', 'Expected JSON array/object.', array( 'status' => 400 ) );
}

function ftes_clear_cache() {
	if ( class_exists( '\Elementor\Plugin' ) && isset( \Elementor\Plugin::$instance->files_manager ) ) {
		\Elementor\Plugin::$instance->files_manager->clear_cache();
	}
}

/**
 * Save Elementor data on a post (page or library template).
 *
 * @param int    $id       Post ID.
 * @param array  $data     Elements.
 * @param string $type     Template type (wp-page, header, footer, loop-item, section ...).
 * @param string $template Page template slug (pages only).
 */
function ftes_save_elementor( $id, array $data, $type, $template = '' ) {
	update_post_meta( $id, '_elementor_edit_mode', 'builder' );
	update_post_meta( $id, '_elementor_template_type', $type );
	if ( $template ) {
		update_post_meta( $id, '_wp_page_template', $template );
	}
	if ( defined( 'ELEMENTOR_VERSION' ) ) {
		update_post_meta( $id, '_elementor_version', ELEMENTOR_VERSION );
	}
	update_post_meta( $id, '_elementor_data', wp_slash( wp_json_encode( $data ) ) );
}

/* ------------------------------------------------------------------ routes */

function ftes_info() {
	$kit = (int) get_option( 'elementor_active_kit' );
	return rest_ensure_response(
		array(
			'bridge'        => FTES_VERSION,
			'wordpress'     => get_bloginfo( 'version' ),
			'elementor'     => defined( 'ELEMENTOR_VERSION' ) ? ELEMENTOR_VERSION : null,
			'elementor_pro' => defined( 'ELEMENTOR_PRO_VERSION' ) ? ELEMENTOR_PRO_VERSION : null,
			'acf'           => function_exists( 'acf_get_setting' ) ? acf_get_setting( 'version' ) : null,
			'acf_pro'       => function_exists( 'acf_pro_is_license_active' ) || defined( 'ACF_PRO' ),
			'kit_id'        => $kit ? $kit : null,
			'front_page'    => (int) get_option( 'page_on_front' ),
			'can_edit'      => ftes_can_edit(),
			'can_manage'    => ftes_can_manage(),
		)
	);
}

function ftes_page( WP_REST_Request $r ) {
	$data = ftes_decode( $r->get_param( 'elementor_data' ) );
	if ( is_wp_error( $data ) ) {
		return $data;
	}
	$status = (string) $r->get_param( 'status' );
	$status = in_array( $status, array( 'draft', 'publish', 'private' ), true ) ? $status : 'draft';
	$tpl    = (string) $r->get_param( 'template' );
	$tpl    = in_array( $tpl, array( 'elementor_canvas', 'elementor_header_footer', 'default' ), true ) ? $tpl : 'default';
	$arr    = array(
		'post_type'     => 'page',
		'post_status'   => $status,
		'post_title'    => sanitize_text_field( (string) $r->get_param( 'title' ) ),
		'page_template' => 'default',
	);
	$slug   = sanitize_title( (string) $r->get_param( 'slug' ) );
	if ( $slug ) {
		$arr['post_name'] = $slug;
	}
	$pid = absint( $r->get_param( 'page_id' ) );
	if ( $pid ) {
		$old = get_post( $pid );
		if ( ! $old || 'page' !== $old->post_type || ! current_user_can( 'edit_post', $pid ) ) {
			return new WP_Error( 'ftes_forbidden', 'Page not found or not editable.', array( 'status' => 403 ) );
		}
		$arr['ID'] = $pid;
		$id        = wp_update_post( wp_slash( $arr ), true );
	} else {
		$id = wp_insert_post( wp_slash( $arr ), true );
	}
	if ( is_wp_error( $id ) ) {
		return $id;
	}
	ftes_save_elementor( $id, $data, 'wp-page', $tpl );
	$ps = $r->get_param( 'page_settings' );
	if ( is_array( $ps ) ) {
		update_post_meta( $id, '_elementor_page_settings', $ps );
	}
	if ( $r->get_param( 'front_page' ) ) {
		update_option( 'show_on_front', 'page' );
		update_option( 'page_on_front', $id );
	}
	ftes_clear_cache();
	return rest_ensure_response(
		array(
			'id'     => (int) $id,
			'status' => get_post_status( $id ),
			'link'   => get_permalink( $id ),
			'edit'   => admin_url( 'post.php?post=' . (int) $id . '&action=elementor' ),
		)
	);
}

function ftes_page_get( WP_REST_Request $r ) {
	$id   = absint( $r['id'] );
	$post = get_post( $id );
	if ( ! $post || ! current_user_can( 'edit_post', $id ) ) {
		return new WP_Error( 'ftes_forbidden', 'Not found or not editable.', array( 'status' => 403 ) );
	}
	$raw = get_post_meta( $id, '_elementor_data', true );
	return rest_ensure_response(
		array(
			'id'             => $id,
			'title'          => $post->post_title,
			'status'         => $post->post_status,
			'link'           => get_permalink( $id ),
			'elementor_data' => is_string( $raw ) ? json_decode( $raw, true ) : $raw,
		)
	);
}

/**
 * Elementor Kit: global colors, typography, layout (container width, breakpoints...).
 */
function ftes_kit( WP_REST_Request $r ) {
	$kit_id = (int) get_option( 'elementor_active_kit' );
	if ( ! $kit_id || ! get_post( $kit_id ) ) {
		return new WP_Error( 'ftes_no_kit', 'No active Elementor Kit. Is Elementor active?', array( 'status' => 404 ) );
	}
	$cur = get_post_meta( $kit_id, '_elementor_page_settings', true );
	$cur = is_array( $cur ) ? $cur : array();
	if ( 'POST' !== $r->get_method() ) {
		return rest_ensure_response(
			array(
				'kit_id'   => $kit_id,
				'settings' => $cur,
			)
		);
	}
	$in = ftes_decode( $r->get_param( 'settings' ) );
	if ( is_wp_error( $in ) ) {
		return $in;
	}
	$merged = array_merge( $cur, $in );
	update_post_meta( $kit_id, '_elementor_page_settings', $merged );
	ftes_clear_cache();
	return rest_ensure_response(
		array(
			'kit_id'       => $kit_id,
			'updated_keys' => array_keys( $in ),
		)
	);
}

/**
 * Elementor Library template: header, footer, loop-item, section, container ...
 * conditions (Elementor Pro Theme Builder): e.g. ["include/general"].
 */
function ftes_template( WP_REST_Request $r ) {
	$data = ftes_decode( $r->get_param( 'elementor_data' ) );
	if ( is_wp_error( $data ) ) {
		return $data;
	}
	$type = sanitize_key( (string) $r->get_param( 'type' ) );
	if ( ! in_array( $type, array( 'header', 'footer', 'loop-item', 'section', 'container', 'page', 'single-post', 'archive', 'single-page', 'error-404' ), true ) ) {
		return new WP_Error( 'ftes_bad_type', 'Unsupported template type.', array( 'status' => 400 ) );
	}
	$id = wp_insert_post(
		wp_slash(
			array(
				'post_type'   => 'elementor_library',
				'post_status' => 'publish',
				'post_title'  => sanitize_text_field( (string) $r->get_param( 'title' ) ),
			)
		),
		true
	);
	if ( is_wp_error( $id ) ) {
		return $id;
	}
	wp_set_object_terms( $id, $type, 'elementor_library_type' );
	ftes_save_elementor( $id, $data, $type );
	$cond = $r->get_param( 'conditions' );
	if ( is_array( $cond ) && $cond ) {
		update_post_meta( $id, '_elementor_conditions', array_map( 'sanitize_text_field', $cond ) );
	}
	ftes_clear_cache();
	return rest_ensure_response(
		array(
			'id'   => (int) $id,
			'type' => $type,
			'edit' => admin_url( 'post.php?post=' . (int) $id . '&action=elementor' ),
		)
	);
}

/* ---- CPT + ACF (dynamic content) */

function ftes_register_stored_post_types() {
	$schema = get_option( 'ftes_schema', array() );
	foreach ( (array) ( $schema['post_types'] ?? array() ) as $pt ) {
		if ( empty( $pt['slug'] ) ) {
			continue;
		}
		register_post_type(
			sanitize_key( $pt['slug'] ),
			array(
				'label'         => sanitize_text_field( $pt['label'] ?? $pt['slug'] ),
				'public'        => true,
				'show_in_rest'  => true,
				'has_archive'   => ! empty( $pt['has_archive'] ),
				'menu_icon'     => sanitize_text_field( $pt['icon'] ?? 'dashicons-admin-post' ),
				'supports'      => array_map( 'sanitize_key', $pt['supports'] ?? array( 'title', 'editor', 'thumbnail' ) ),
				'rewrite'       => array( 'slug' => sanitize_title( $pt['rewrite'] ?? $pt['slug'] ) ),
			)
		);
	}
}
add_action( 'init', 'ftes_register_stored_post_types', 5 );

function ftes_import_acf_groups( array $groups ) {
	$done = array();
	if ( ! function_exists( 'acf_import_field_group' ) ) {
		return $done;
	}
	foreach ( $groups as $g ) {
		if ( empty( $g['key'] ) ) {
			continue;
		}
		$existing = function_exists( 'acf_get_field_group' ) ? acf_get_field_group( $g['key'] ) : false;
		if ( $existing ) {
			$g['ID'] = $existing['ID'];
		}
		$res    = acf_import_field_group( $g );
		$done[] = $g['key'] . ':' . ( is_array( $res ) && ! empty( $res['ID'] ) ? $res['ID'] : 'ok' );
	}
	return $done;
}

function ftes_schema( WP_REST_Request $r ) {
	$in = ftes_decode( $r->get_params() );
	if ( is_wp_error( $in ) ) {
		return $in;
	}
	$schema = array(
		'post_types' => is_array( $in['post_types'] ?? null ) ? $in['post_types'] : array(),
	);
	update_option( 'ftes_schema', $schema, false );
	ftes_register_stored_post_types();
	flush_rewrite_rules();
	$acf = ftes_import_acf_groups( is_array( $in['field_groups'] ?? null ) ? $in['field_groups'] : array() );
	return rest_ensure_response(
		array(
			'post_types'  => array_column( $schema['post_types'], 'slug' ),
			'acf_groups'  => $acf,
			'acf_active'  => function_exists( 'acf_import_field_group' ),
		)
	);
}

function ftes_posts( WP_REST_Request $r ) {
	$type  = sanitize_key( (string) $r->get_param( 'post_type' ) );
	$items = $r->get_param( 'items' );
	if ( ! post_type_exists( $type ) || ! is_array( $items ) ) {
		return new WP_Error( 'ftes_bad_request', 'Unknown post_type or items missing.', array( 'status' => 400 ) );
	}
	$ids = array();
	foreach ( $items as $it ) {
		$status = $it['status'] ?? 'publish';
		$status = in_array( $status, array( 'draft', 'publish' ), true ) ? $status : 'publish';
		$id     = wp_insert_post(
			wp_slash(
				array(
					'post_type'    => $type,
					'post_status'  => $status,
					'post_title'   => sanitize_text_field( $it['title'] ?? '' ),
					'post_content' => wp_kses_post( $it['content'] ?? '' ),
				)
			),
			true
		);
		if ( is_wp_error( $id ) ) {
			continue;
		}
		foreach ( (array) ( $it['meta'] ?? array() ) as $k => $v ) {
			if ( function_exists( 'update_field' ) ) {
				update_field( sanitize_key( $k ), $v, $id );
			} else {
				update_post_meta( $id, sanitize_key( $k ), $v );
			}
		}
		if ( ! empty( $it['thumbnail_id'] ) ) {
			set_post_thumbnail( $id, absint( $it['thumbnail_id'] ) );
		}
		$ids[] = (int) $id;
	}
	return rest_ensure_response( array( 'ids' => $ids ) );
}

/* ---- menus */

function ftes_menu( WP_REST_Request $r ) {
	$name  = sanitize_text_field( (string) $r->get_param( 'name' ) );
	$items = $r->get_param( 'items' );
	if ( '' === $name || ! is_array( $items ) ) {
		return new WP_Error( 'ftes_bad_request', 'name and items required.', array( 'status' => 400 ) );
	}
	$menu = wp_get_nav_menu_object( $name );
	$mid  = $menu ? (int) $menu->term_id : (int) wp_create_nav_menu( $name );
	$add  = function ( $list, $parent ) use ( &$add, $mid ) {
		foreach ( $list as $i => $it ) {
			$args = array(
				'menu-item-title'     => sanitize_text_field( $it['title'] ?? '' ),
				'menu-item-status'    => 'publish',
				'menu-item-position'  => $i + 1,
				'menu-item-parent-id' => $parent,
			);
			if ( ! empty( $it['page_id'] ) ) {
				$args['menu-item-type']      = 'post_type';
				$args['menu-item-object']    = 'page';
				$args['menu-item-object-id'] = absint( $it['page_id'] );
			} else {
				$args['menu-item-type'] = 'custom';
				$args['menu-item-url']  = esc_url_raw( $it['url'] ?? '#' );
			}
			$new = wp_update_nav_menu_item( $mid, 0, $args );
			if ( ! is_wp_error( $new ) && ! empty( $it['children'] ) ) {
				$add( $it['children'], (int) $new );
			}
		}
	};
	$add( $items, 0 );
	$loc = sanitize_key( (string) $r->get_param( 'location' ) );
	if ( $loc ) {
		$locs         = get_theme_mod( 'nav_menu_locations', array() );
		$locs[ $loc ] = $mid;
		set_theme_mod( 'nav_menu_locations', $locs );
	}
	return rest_ensure_response( array( 'menu_id' => $mid ) );
}

/* ---- custom fonts (the fonts the design uses; printed as @font-face on the front end) */

function ftes_fonts( WP_REST_Request $r ) {
	$family = sanitize_text_field( (string) $r->get_param( 'family' ) );
	$weight = preg_replace( '/[^0-9]/', '', (string) $r->get_param( 'weight' ) );
	$style  = 'italic' === $r->get_param( 'style' ) ? 'italic' : 'normal';
	$ext    = strtolower( sanitize_key( (string) $r->get_param( 'ext' ) ) );
	$bin    = base64_decode( (string) $r->get_param( 'data' ), true ); // phpcs:ignore WordPress.PHP.DiscouragedPHPFunctions
	if ( '' === $family || ! in_array( $ext, array( 'woff2', 'woff', 'ttf', 'otf' ), true ) || false === $bin || strlen( $bin ) > 8 * MB_IN_BYTES ) {
		return new WP_Error( 'ftes_bad_font', 'family, ext (woff2|woff|ttf|otf) and base64 data required.', array( 'status' => 400 ) );
	}
	$up   = wp_upload_dir();
	$dir  = trailingslashit( $up['basedir'] ) . 'fte-fonts';
	wp_mkdir_p( $dir );
	$file = sanitize_file_name( strtolower( $family . '-' . $weight . '-' . $style ) . '.' . $ext );
	file_put_contents( $dir . '/' . $file, $bin ); // phpcs:ignore WordPress.WP.AlternativeFunctions
	$fonts = get_option( 'ftes_fonts', array() );
	$fonts = array_values(
		array_filter(
			$fonts,
			function ( $f ) use ( $family, $weight, $style ) {
				return ! ( $f['family'] === $family && $f['weight'] === $weight && $f['style'] === $style );
			}
		)
	);
	$fonts[] = array(
		'family' => $family,
		'weight' => $weight,
		'style'  => $style,
		'file'   => $file,
		'ext'    => $ext,
	);
	update_option( 'ftes_fonts', $fonts, false );
	return rest_ensure_response( array( 'fonts' => count( $fonts ) ) );
}

/**
 * Print @font-face for uploaded design fonts (this is the approved "theme CSS" for fonts, skill 135).
 */
function ftes_print_fonts() {
	$fonts = get_option( 'ftes_fonts', array() );
	if ( ! $fonts ) {
		return;
	}
	$up    = wp_upload_dir();
	$base  = trailingslashit( $up['baseurl'] ) . 'fte-fonts/';
	$fmt   = array(
		'woff2' => 'woff2',
		'woff'  => 'woff',
		'ttf'   => 'truetype',
		'otf'   => 'opentype',
	);
	$css = '';
	foreach ( $fonts as $f ) {
		$css .= sprintf(
			'@font-face{font-family:"%1$s";src:url("%2$s") format("%3$s");font-weight:%4$s;font-style:%5$s;font-display:swap}',
			esc_attr( $f['family'] ),
			esc_url( $base . $f['file'] ),
			esc_attr( $fmt[ $f['ext'] ] ),
			esc_attr( $f['weight'] ),
			esc_attr( $f['style'] )
		);
	}
	echo '<style id="ftes-fonts">' . $css . "</style>\n"; // phpcs:ignore WordPress.Security.EscapeOutput
}
add_action( 'wp_head', 'ftes_print_fonts', 2 );

/* ---- site settings (permalinks, title) */

function ftes_settings( WP_REST_Request $r ) {
	if ( $r->get_param( 'permalink_structure' ) ) {
		update_option( 'permalink_structure', sanitize_text_field( (string) $r->get_param( 'permalink_structure' ) ) );
		flush_rewrite_rules();
	}
	if ( $r->get_param( 'blogname' ) ) {
		update_option( 'blogname', sanitize_text_field( (string) $r->get_param( 'blogname' ) ) );
	}
	return rest_ensure_response( array( 'ok' => true ) );
}

add_action(
	'rest_api_init',
	function () {
		$ns = 'ftes/v1';
		$rt = function ( $route, $methods, $cb, $perm ) use ( $ns ) {
			register_rest_route(
				$ns,
				$route,
				array(
					'methods'             => $methods,
					'callback'            => $cb,
					'permission_callback' => $perm,
				)
			);
		};
		$rt( '/info', 'GET', 'ftes_info', 'ftes_can_edit' );
		$rt( '/page', 'POST', 'ftes_page', 'ftes_can_edit' );
		$rt( '/page/(?P<id>\d+)', 'GET', 'ftes_page_get', 'ftes_can_edit' );
		$rt( '/template', 'POST', 'ftes_template', 'ftes_can_manage' );
		$rt( '/kit', array( 'GET', 'POST' ), 'ftes_kit', 'ftes_can_manage' );
		$rt( '/schema', 'POST', 'ftes_schema', 'ftes_can_manage' );
		$rt( '/posts', 'POST', 'ftes_posts', 'ftes_can_manage' );
		$rt( '/menu', 'POST', 'ftes_menu', 'ftes_can_manage' );
		$rt( '/settings', 'POST', 'ftes_settings', 'ftes_can_manage' );
		$rt( '/fonts', 'POST', 'ftes_fonts', 'ftes_can_manage' );
	}
);

add_filter(
	'rest_pre_dispatch',
	function ( $result, $server, $request ) {
		if ( 0 === strpos( $request->get_route(), '/ftes/v1/' ) ) {
			$len = isset( $_SERVER['CONTENT_LENGTH'] ) ? (int) $_SERVER['CONTENT_LENGTH'] : 0; // phpcs:ignore WordPress.Security.ValidatedSanitizedInput
			if ( $len > FTES_MAX_BODY ) {
				return new WP_Error( 'ftes_too_large', 'Request body too large.', array( 'status' => 413 ) );
			}
		}
		return $result;
	},
	10,
	3
);
